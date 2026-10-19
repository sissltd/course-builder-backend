"""Ticket lifecycle for the admin Tickets page and the creator's Help >
Tickets tab (Figma): status changes (single and bulk), the reply thread,
and the status-count cards."""

from uuid import UUID

from django.db import transaction
from django.db.models import Count, QuerySet
from django.utils import timezone
from rest_framework import exceptions

from api.authorization import codenames
from api.authorization.services import permission_service
from api.notification.models import Notification
from api.support.enums import (
    SupportRequestKind,
    SupportRequestStatus,
    SupportTicketMessageKind,
)
from api.support.models import SupportRequest, SupportTicketMessage
from api.support.services import support_service
from api.users.models import User

#: Statuses an admin can move a ticket to from each status. CLOSED is final;
#: a RESOLVED ticket can be reopened (In-progress) or closed.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    SupportRequestStatus.OPEN: frozenset(
        {
            SupportRequestStatus.IN_PROGRESS,
            SupportRequestStatus.RESOLVED,
            SupportRequestStatus.CLOSED,
        }
    ),
    SupportRequestStatus.IN_PROGRESS: frozenset(
        {SupportRequestStatus.RESOLVED, SupportRequestStatus.CLOSED}
    ),
    SupportRequestStatus.RESOLVED: frozenset(
        {SupportRequestStatus.IN_PROGRESS, SupportRequestStatus.CLOSED}
    ),
    SupportRequestStatus.CLOSED: frozenset(),
}


def tickets() -> QuerySet[SupportRequest]:
    """Every ticket, with the relations the list/detail serializers read."""

    return SupportRequest.objects.filter(kind=SupportRequestKind.TICKET).select_related(
        "submitted_by", "assigned_to"
    )


def status_counts() -> dict[str, int]:
    """Figma summary cards: Opened / In-Progress / Resolved / Closed."""

    counts = dict(
        SupportRequest.objects.filter(kind=SupportRequestKind.TICKET)
        .values_list("status")
        .annotate(total=Count("id"))
    )
    return {
        status.lower(): counts.get(status, 0) for status in SupportRequestStatus.values
    }


def _transition_error(ticket: SupportRequest, status: str) -> str | None:
    if status == ticket.status:
        return f"Ticket is already '{ticket.get_status_display()}'."
    if status not in ALLOWED_TRANSITIONS[ticket.status]:
        return (
            f"Ticket cannot move from '{ticket.get_status_display()}' to "
            f"'{SupportRequestStatus(status).label}'."
        )
    return None


def _apply_status(ticket: SupportRequest, *, actor: User, status: str) -> None:
    """Write the change, its thread entry, and tell the submitter."""

    previous = ticket.status
    now = timezone.now()
    ticket.status = status
    if ticket.assigned_to_id is None:
        ticket.assigned_to = actor
    if status == SupportRequestStatus.RESOLVED:
        ticket.resolved_by = actor
        ticket.resolved_at = now
    ticket.save(
        update_fields=[
            "status",
            "assigned_to",
            "resolved_by",
            "resolved_at",
            "updated_datetime",
        ]
    )
    SupportTicketMessage.objects.create(
        ticket=ticket,
        kind=SupportTicketMessageKind.STATUS_CHANGE,
        author=actor,
        from_status=previous,
        to_status=status,
    )

    label = SupportRequestStatus(status).label
    if ticket.submitted_by_id:
        Notification.emit_in_app_notification(
            receivers=[ticket.submitted_by],
            title=f"Ticket {label.lower()}",
            content=f"Your ticket '{ticket.title}' is now {label}.",
            metadata={"support_request_id": ticket.id, "status": status},
        )
    support_service.email_submitter(
        request=ticket,
        subject=f"Ticket {label.lower()}: {ticket.title}",
        template_name="emails/support_ticket_updated",
        context={"update": f"Your ticket status changed to {label}."},
    )


def _require_ticket(ticket: SupportRequest) -> None:
    if ticket.kind != SupportRequestKind.TICKET:
        raise exceptions.NotFound("Ticket not found.")


@transaction.atomic
def change_status(
    *, ticket: SupportRequest, actor: User, status: str
) -> SupportRequest:
    """Admin 'Close ticket' / 'Mark as in-progress' / 'Mark as resolved'."""

    permission_service.require_permission(actor, codenames.SUPPORT_MANAGE_REQUESTS)
    _require_ticket(ticket)
    ticket = SupportRequest.objects.select_for_update().get(pk=ticket.pk)
    error = _transition_error(ticket, status)
    if error:
        raise exceptions.ValidationError({"status": [error]})
    _apply_status(ticket, actor=actor, status=status)
    return ticket


@transaction.atomic
def bulk_change_status(
    *, ticket_ids: list[UUID], actor: User, status: str
) -> dict[str, list]:
    """The checkbox bulk bar. A selection can mix statuses (e.g. closing an
    already-Closed row), so ineligible tickets are skipped with a reason
    instead of failing the whole batch."""

    permission_service.require_permission(actor, codenames.SUPPORT_MANAGE_REQUESTS)
    found = {
        ticket.id: ticket
        for ticket in SupportRequest.objects.select_for_update(of=("self",))
        .filter(kind=SupportRequestKind.TICKET, id__in=ticket_ids)
        .select_related("submitted_by")
    }
    updated: list[SupportRequest] = []
    skipped: list[dict] = []
    for ticket_id in dict.fromkeys(ticket_ids):
        ticket = found.get(ticket_id)
        if ticket is None:
            skipped.append({"id": ticket_id, "reason": "Ticket not found."})
            continue
        error = _transition_error(ticket, status)
        if error:
            skipped.append({"id": ticket_id, "reason": error})
            continue
        _apply_status(ticket, actor=actor, status=status)
        updated.append(ticket)
    return {"updated": updated, "skipped": skipped}


@transaction.atomic
def add_reply(
    *, ticket: SupportRequest, author: User, body: str
) -> SupportTicketMessage:
    """'Add a response to this ticket'. The submitter and support staff share
    one thread; whichever side writes, the other side is told."""

    _require_ticket(ticket)
    is_submitter = ticket.submitted_by_id == author.id
    if not is_submitter:
        permission_service.require_permission(author, codenames.SUPPORT_MANAGE_REQUESTS)
    ticket = SupportRequest.objects.select_for_update().get(pk=ticket.pk)
    if ticket.status == SupportRequestStatus.CLOSED:
        raise exceptions.ValidationError(
            {"body": ["This ticket is closed and no longer accepts replies."]}
        )

    message = SupportTicketMessage.objects.create(
        ticket=ticket,
        kind=SupportTicketMessageKind.REPLY,
        author=author,
        body=body,
    )
    update_fields = ["updated_datetime"]
    if not is_submitter and ticket.assigned_to_id is None:
        ticket.assigned_to = author
        update_fields.append("assigned_to")
    ticket.save(update_fields=update_fields)

    if is_submitter:
        support_service.notify_staff(
            request=ticket,
            title="New reply on a ticket",
            content=f"{ticket.email} replied to '{ticket.title}'.",
            receivers=[ticket.assigned_to] if ticket.assigned_to_id else None,
        )
    else:
        if ticket.submitted_by_id:
            Notification.emit_in_app_notification(
                receivers=[ticket.submitted_by],
                title="Support replied to your ticket",
                content=body,
                metadata={"support_request_id": ticket.id},
            )
        support_service.email_submitter(
            request=ticket,
            subject=f"RE: {ticket.title}",
            template_name="emails/support_ticket_updated",
            context={"update": "Support replied to your ticket:", "reply": body},
        )
    return message

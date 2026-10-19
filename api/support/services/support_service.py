import logging
from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework import exceptions

from api.authentication.services import activity_service
from api.authorization import codenames
from api.authorization.services import permission_service
from api.notification.models import Notification
from api.notification.services.email_service import send_templated_email
from api.support.enums import SupportRequestKind, SupportRequestStatus
from api.support.models import SupportRequest
from api.users.enums import UserActivityActionEnums, UserActivityCategoryEnums
from api.users.models import User

logger = logging.getLogger(__name__)

#: PRD Section 30 SLA table: dispute resolution < 7 business days.
APPEAL_SLA_BUSINESS_DAYS = 7

_KIND_LABELS = {
    SupportRequestKind.CONTACT: "contact message",
    SupportRequestKind.TICKET: "support ticket",
    SupportRequestKind.APPEAL: "appeal",
}


def add_business_days(start: datetime, days: int) -> datetime:
    """`start` plus `days` Monday-Friday days (public holidays not modelled)."""

    current = start
    remaining = days
    while remaining > 0:
        current += timedelta(days=1)
        if current.weekday() < 5:
            remaining -= 1
    return current


def notify_staff(
    *, request: SupportRequest, title: str, content: str, receivers=None
) -> None:
    """In-app notice to `receivers`, defaulting to everyone who can manage
    support requests."""

    staff = list(
        receivers
        if receivers is not None
        else permission_service.users_with_permission(codenames.SUPPORT_MANAGE_REQUESTS)
    )
    if not staff:
        return
    Notification.emit_in_app_notification(
        receivers=staff,
        title=title,
        content=content,
        metadata={"support_request_id": request.id, "kind": request.kind},
    )


def _notify_staff(*, request: SupportRequest) -> None:
    label = _KIND_LABELS[request.kind]
    notify_staff(
        request=request,
        title=f"New {label}",
        content=f"{request.email} sent a {label}"
        + (f": {request.title}" if request.title else "."),
    )


def email_submitter(
    *, request: SupportRequest, subject: str, template_name: str, context: dict
) -> None:
    """Email the address typed on the form once the transaction commits.
    A delivery failure is logged, never raised: the ticket change stands."""

    def _send() -> None:
        try:
            send_templated_email(
                receivers=[request.email],
                subject=subject,
                template_name=template_name,
                context={
                    "first_name": request.first_name or "there",
                    "ticket_id": str(request.id),
                    "ticket_title": request.title,
                    **context,
                },
            )
        except Exception:  # noqa: BLE001 - see docstring
            logger.exception("support request %s email failed", request.id)

    transaction.on_commit(_send)


@transaction.atomic
def submit_contact(
    *,
    user: User | None,
    first_name: str,
    last_name: str,
    email: str,
    country: str,
    message: str,
) -> SupportRequest:
    """Record a Contact-us message (public; `user` is set when signed in)."""

    request = SupportRequest.objects.create(
        kind=SupportRequestKind.CONTACT,
        submitted_by=user,
        first_name=first_name,
        last_name=last_name,
        email=email,
        country=country,
        message=message,
    )
    _notify_staff(request=request)
    return request


@transaction.atomic
def submit_titled_request(
    *,
    user: User,
    kind: str,
    title: str,
    email: str,
    description: str,
    web_link: str = "",
    priority: str = "",
) -> SupportRequest:
    """Record a ticket or an appeal (same form shape: title, email, link,
    description). Tickets also carry a priority and email the submitter a
    confirmation (Figma: "check your email to track the progress"). Appeals
    get the PRD's 7-business-day resolution deadline and are written to the
    submitter's activity log."""

    if kind not in (SupportRequestKind.TICKET, SupportRequestKind.APPEAL):
        raise ValueError(f"Unsupported kind for a titled request: {kind}")

    is_appeal = kind == SupportRequestKind.APPEAL
    request = SupportRequest.objects.create(
        kind=kind,
        submitted_by=user,
        first_name=user.first_name,
        last_name=user.last_name,
        email=email,
        title=title,
        web_link=web_link,
        message=description,
        priority="" if is_appeal else priority,
        due_at=(
            add_business_days(timezone.now(), APPEAL_SLA_BUSINESS_DAYS)
            if is_appeal
            else None
        ),
    )
    _notify_staff(request=request)
    if not is_appeal:
        email_submitter(
            request=request,
            subject=f"Ticket created: {title}",
            template_name="emails/support_ticket_created",
            context={"description": description},
        )
    if is_appeal:
        activity_service.log_activity(
            user=user,
            category=UserActivityCategoryEnums.APPROVAL,
            action=UserActivityActionEnums.APPEAL_SUBMITTED,
            summary=f"You requested an appeal: '{title}'.",
            target=request,
        )
    return request


@transaction.atomic
def resolve_request(
    *, support_request: SupportRequest, actor: User, notes: str = ""
) -> SupportRequest:
    """Close an Open request with optional notes and tell the submitter.
    Final: a resolved request cannot be re-resolved (PRD: decision is final)."""

    permission_service.require_permission(actor, codenames.SUPPORT_MANAGE_REQUESTS)
    if support_request.status != SupportRequestStatus.OPEN:
        raise exceptions.ValidationError(
            f"Request cannot be resolved from status '{support_request.status}'."
        )

    support_request.status = SupportRequestStatus.RESOLVED
    support_request.resolution_notes = notes
    support_request.resolved_by = actor
    support_request.resolved_at = timezone.now()
    support_request.save(
        update_fields=[
            "status",
            "resolution_notes",
            "resolved_by",
            "resolved_at",
            "updated_datetime",
        ]
    )

    if support_request.submitted_by_id:
        label = _KIND_LABELS[support_request.kind]
        Notification.emit_in_app_notification(
            receivers=[support_request.submitted_by],
            title=f"Your {label} was resolved",
            content=notes or f"Your {label} has been reviewed and resolved.",
            metadata={"support_request_id": support_request.id},
        )
    return support_request

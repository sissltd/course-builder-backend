"""Superadmin decision surface for course idea submissions.

Everything here is reversible by design: approve/reject can be flipped
in any direction at any time, and every flip records its immediate
webhook event so the developer's queue reference suffix tracks reality.

Payout semantics live on the submission, not in code paths: a bypassed
submission is marked no-payout at approval time; whether wallet credit
actually happens is the resulting-course production flow's concern (not
built yet - MIE courses are ideas until produced).
"""

from django.db import transaction
from django.utils import timezone
from rest_framework import exceptions

from api.mie.enums import SubmissionStatus, WebhookEventType
from api.mie.models import CourseSubmission, SubmissionRejectionReason, WebhookEvent
from api.users.enums import UserRole
from api.users.permissions import require_role

DECIDED_STATUSES = (SubmissionStatus.APPROVED, SubmissionStatus.REJECTED)


def decide_submission(
    *,
    actor,
    submission: CourseSubmission,
    approve: bool,
    rejection_reason: SubmissionRejectionReason | None = None,
    rejection_note: str = "",
) -> CourseSubmission:
    """Approve or reject an idea; callable from any state, any number of
    times. Returns the refreshed submission; caller serializes it.

    Reversal side effects:
    * APPROVED -> REJECTED with a linked resulting_course flags that
      course out of production (unpublished + flagged for review) rather
      than deleting it, and keeps the link so re-approval relinks.
    * REJECTED -> APPROVED clears stale rejection metadata.
    """

    require_role(actor, (UserRole.SUPER_ADMIN,))
    new_status = SubmissionStatus.APPROVED if approve else SubmissionStatus.REJECTED

    if not approve:
        if rejection_reason is None and submission.status != SubmissionStatus.REJECTED:
            raise exceptions.ValidationError(
                {"rejection_reason": ["A rejection reason is required to reject."]}
            )
        _flag_resulting_course_if_any(submission)

    with transaction.atomic():
        submission.status = new_status
        submission.decided_at = timezone.now()
        submission.decided_by = actor
        if approve:
            submission.rejection_reason = None
            submission.rejection_note = ""
        else:
            submission.rejection_reason = rejection_reason
            submission.rejection_note = rejection_note or submission.rejection_note
        submission.save()

        WebhookEvent.objects.create(
            submission=submission,
            event_type=WebhookEventType.SUBMISSION_APPROVED
            if approve
            else WebhookEventType.SUBMISSION_REJECTED,
            payload=_decision_payload(submission),
        )
    return submission


def set_demand_signals(
    *,
    actor,
    submission: CourseSubmission,
    demand_score: int | None,
    estimated_monthly_earnings=None,
) -> CourseSubmission:
    """Record the admin-entered market-research prioritisation signals."""

    require_role(actor, (UserRole.SUPER_ADMIN,))
    if demand_score is not None and not 0 <= demand_score <= 100:
        raise exceptions.ValidationError(
            {"demand_score": ["Demand score must be between 0 and 100."]}
        )

    submission.demand_score = demand_score
    if estimated_monthly_earnings is not None:
        submission.estimated_monthly_earnings = estimated_monthly_earnings
    submission.save(
        update_fields=[
            "demand_score",
            "estimated_monthly_earnings",
            "updated_datetime",
        ]
    )
    return submission


def set_payout_bypass(*, actor, submission: CourseSubmission, bypass: bool) -> CourseSubmission:
    """Mark one specific idea as no-payout and tell the developer now.

    Fires SUBMISSION_PAYOUT_BYPASS_UPDATED regardless of status change -
    this is a commercial signal, not a pipeline move, so it gets its own
    event type rather than overloading approve/reject.
    """

    require_role(actor, (UserRole.SUPER_ADMIN,))
    if submission.payout_bypass == bypass:
        raise exceptions.ValidationError(
            {"payout_bypass": [f"Payout bypass is already {'set' if bypass else 'clear'}."]}
        )

    submission.payout_bypass = bypass
    submission.save(update_fields=["payout_bypass", "updated_datetime"])

    WebhookEvent.objects.create(
        submission=submission,
        event_type=WebhookEventType.SUBMISSION_PAYOUT_BYPASS_UPDATED,
        payload=_bypass_payload(submission),
    )
    return submission


def _flag_resulting_course_if_any(submission: CourseSubmission) -> None:
    """Unpublish + park a produced course when its idea is reversed.

    The link survives so a later re-approval finds it again instead of
    creating a duplicate. No-op today (ideas have no course yet) but the
    contract holds for when production exists.
    """

    from api.courses.enums import CourseStatus

    course = submission.resulting_course
    if course is None:
        return
    if course.status == CourseStatus.PUBLISHED:
        course.status = CourseStatus.NEEDS_REVISION
        course.save(update_fields=["status", "updated_datetime"])


def _base_payload(submission: CourseSubmission) -> dict:
    """Envelope shape shared with submission_service._event_payload.

    The dispatcher wires only `payload["submission"]` onto the network
    (webhook_dispatcher.render_body), so every event-producing path must
    nest under that key - a flat dict here would deliver an empty
    submission object to the developer.
    """

    return {
        "submission": {
            "reference": submission.public_reference,
            "status": submission.status,
            "title": submission.title,
        },
        "developer_email": submission.developer.email,
    }


def _decision_payload(submission: CourseSubmission) -> dict:
    payload = _base_payload(submission)
    if submission.status == SubmissionStatus.REJECTED:
        payload["submission"]["rejection_reason"] = (
            submission.rejection_reason.label if submission.rejection_reason else None
        )
        payload["submission"]["rejection_note"] = submission.rejection_note
    return payload


def _bypass_payload(submission: CourseSubmission) -> dict:
    payload = _base_payload(submission)
    payload["submission"]["payout_bypass"] = submission.payout_bypass
    return payload

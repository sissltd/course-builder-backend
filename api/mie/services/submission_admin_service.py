"""Superadmin decision surface for course idea submissions.

Everything here is reversible by design: approve/reject can be flipped
in any direction at any time, and every flip records its immediate
webhook event so the developer's queue reference suffix tracks reality.

A decision never changes a linked `resulting_course`. That course lives by
the course domain's lifecycle (draft hold, content review seats, QA
verification, one-way publication - see courses.course_service), which has
no path for an outside app to unpublish or park a course. Reversing an idea
keeps the link, so a re-approval finds the same course rather than a
duplicate, and records the linked course on the decision's audit row so
admins can act on it through the course tools.

Payout semantics live on the submission, not in code paths: a developer is
paid per course that is produced from their idea and published, never per
approval, and a bypassed submission is marked no-payout. Nothing here pays
anyone; that belongs to the course side once an idea produces a course (the
idea-to-course bridge is not built yet).
"""

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone
from rest_framework import exceptions

from api.authentication.services.activity_service import log_activity
from api.mie.enums import MieSourceType, SubmissionStatus, WebhookEventType
from api.mie.models import CourseSubmission, SubmissionRejectionReason, WebhookEvent
from api.mie.services import guardrail_service
from api.users.enums import (
    UserActivityActionEnums,
    UserActivityCategoryEnums,
)
from api.users.models import UserActivityLog
from api.authorization import codenames
from api.authorization.services import permission_service

DECIDED_STATUSES = (SubmissionStatus.APPROVED, SubmissionStatus.REJECTED)

BULK_DECISION_LIMIT = 100
"""Most ideas one bulk call may decide - the batch size every write below uses."""

SUMMARY_TITLE_LIMIT = 180
"""Title budget inside an activity summary, which the column caps at 255."""


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

    Deciding an idea is the Writer's job as well as the Super Admin's
    (`mie.approve_topic_proposals`); the rest of the MIE console needs `mie.manage_console`.

    Reversal side effects:
    * APPROVED -> REJECTED leaves any linked resulting_course untouched
      (see the module docstring) and keeps the link so re-approval relinks.
    * REJECTED -> APPROVED clears stale rejection metadata.
    """

    permission_service.require_permission(actor, codenames.MIE_APPROVE_TOPIC_PROPOSALS)
    new_status = SubmissionStatus.APPROVED if approve else SubmissionStatus.REJECTED

    # An already-rejected row may be re-rejected without a fresh reason;
    # any other rejection must carry one.
    if (
        not approve
        and rejection_reason is None
        and submission.status != SubmissionStatus.REJECTED
    ):
        raise exceptions.ValidationError(
            {"rejection_reason": ["A rejection reason is required to reject."]}
        )

    with transaction.atomic():
        submission.status = new_status
        submission.decided_at = timezone.now()
        submission.decided_by = actor
        if approve:
            submission.rejection_reason = None
            submission.rejection_note = ""
        else:
            # Both fall back to what is already stored, so re-rejecting an
            # already-rejected idea without fresh detail preserves rather
            # than wipes it.
            submission.rejection_reason = rejection_reason or submission.rejection_reason
            submission.rejection_note = rejection_note or submission.rejection_note
        submission.save()

        WebhookEvent.objects.create(
            submission=submission,
            event_type=WebhookEventType.SUBMISSION_APPROVED
            if approve
            else WebhookEventType.SUBMISSION_REJECTED,
            payload=_decision_payload(submission),
        )

        # Recorded against the deciding admin: the submitter is an external
        # developer with no row in the platform's user table, so there is no
        # other account this belongs on.
        log_activity(
            user=actor,
            category=UserActivityCategoryEnums.APPROVAL,
            action=UserActivityActionEnums.COURSE_APPROVED
            if approve
            else UserActivityActionEnums.COURSE_REJECTED,
            summary=_decision_summary(approve, submission.title),
            details=_decision_details(submission),
            target=submission,
        )

        # Same transaction as the rejection that feeds it: if this decision
        # rolls back, so does any suspension it caused. Approvals and
        # external developers never reach the breaker.
        if not approve and submission.developer.source_type == MieSourceType.SYSTEM:
            guardrail_service.evaluate_rejection_breaker(
                account=submission.developer, actor=actor
            )
    return submission


def set_demand_signals(
    *,
    actor,
    submission: CourseSubmission,
    demand_score: int | None,
    estimated_monthly_earnings=None,
    category=None,
    difficulty_level=None,
    searches_per_month=None,
    description=None,
) -> CourseSubmission:
    """Record the admin-entered prioritisation signals, and any correction
    to what the submitter sent.

    Everything after `demand_score` is an override: omitted means "leave
    what is stored", which is why a partial edit from the review screen
    cannot blank the fields it did not touch.
    """

    permission_service.require_permission(actor, codenames.MIE_APPROVE_TOPIC_PROPOSALS)
    if demand_score is not None and not 0 <= demand_score <= 100:
        raise exceptions.ValidationError(
            {"demand_score": ["Demand score must be between 0 and 100."]}
        )

    submission.demand_score = demand_score
    updated = ["demand_score", "updated_datetime"]
    for field, value in (
        ("estimated_monthly_earnings", estimated_monthly_earnings),
        ("category", category),
        ("difficulty_level", difficulty_level),
        ("searches_per_month", searches_per_month),
        ("description", description),
    ):
        if value is not None:
            setattr(submission, field, value)
            updated.append(field)

    submission.save(update_fields=updated)
    return submission


def decide_submissions_bulk(
    *,
    actor,
    submission_ids: list,
    approve: bool,
    rejection_reason: SubmissionRejectionReason | None = None,
    rejection_note: str = "",
) -> list[CourseSubmission]:
    """Approve or reject many ideas at once, from the Recommendations screen.

    Validated as one batch, then written as one batch: whatever the number
    of ideas, this costs two SELECTs, one UPDATE and two INSERTs, so a
    fifty-row selection is no more expensive per row than a single one.
    An id matching nothing fails the whole call before any write, so a
    half-applied selection is impossible.

    Each idea still gets its own webhook event and audit row, exactly as a
    one-at-a-time decision would.
    """

    permission_service.require_permission(actor, codenames.MIE_APPROVE_TOPIC_PROPOSALS)
    if len(submission_ids) > BULK_DECISION_LIMIT:
        raise exceptions.ValidationError(
            {"ids": [f"At most {BULK_DECISION_LIMIT} ideas can be decided at once."]}
        )

    submissions = list(
        CourseSubmission.objects.select_related("developer").filter(
            id__in=submission_ids
        )
    )
    found = {str(submission.id) for submission in submissions}
    missing = sorted({str(given) for given in submission_ids} - found)
    if missing:
        raise exceptions.NotFound(f"No submission found for: {', '.join(missing)}.")

    # The same rule decide_submission applies, asked once for the batch.
    if (
        not approve
        and rejection_reason is None
        and any(item.status != SubmissionStatus.REJECTED for item in submissions)
    ):
        raise exceptions.ValidationError(
            {"rejection_reason": ["A rejection reason is required to reject."]}
        )

    now = timezone.now()
    new_status = SubmissionStatus.APPROVED if approve else SubmissionStatus.REJECTED
    for submission in submissions:
        submission.status = new_status
        submission.decided_at = now
        submission.decided_by = actor
        submission.updated_datetime = now
        if approve:
            submission.rejection_reason = None
            submission.rejection_note = ""
        else:
            submission.rejection_reason = rejection_reason or submission.rejection_reason
            submission.rejection_note = rejection_note or submission.rejection_note

    with transaction.atomic():
        CourseSubmission.objects.bulk_update(
            submissions,
            fields=[
                "status",
                "decided_at",
                "decided_by",
                "rejection_reason",
                "rejection_note",
                "updated_datetime",
            ],
            batch_size=BULK_DECISION_LIMIT,
        )
        WebhookEvent.objects.bulk_create(
            [
                WebhookEvent(
                    submission=submission,
                    event_type=WebhookEventType.SUBMISSION_APPROVED
                    if approve
                    else WebhookEventType.SUBMISSION_REJECTED,
                    payload=_decision_payload(submission),
                )
                for submission in submissions
            ],
            batch_size=BULK_DECISION_LIMIT,
        )
        # Built rather than routed through activity_service.log_activity: the
        # helper writes one row per call, which would put an INSERT per idea
        # back into a path whose whole point is a flat cost. Same columns.
        content_type = ContentType.objects.get_for_model(CourseSubmission)
        UserActivityLog.objects.bulk_create(
            [
                UserActivityLog(
                    user=actor,
                    actor_user=actor,
                    category=UserActivityCategoryEnums.APPROVAL,
                    action=UserActivityActionEnums.COURSE_APPROVED
                    if approve
                    else UserActivityActionEnums.COURSE_REJECTED,
                    summary=_decision_summary(approve, submission.title),
                    details={**_decision_details(submission), "bulk": True},
                    activity_datetime=now,
                    content_type=content_type,
                    object_id=str(submission.id),
                )
                for submission in submissions
            ],
            batch_size=BULK_DECISION_LIMIT,
        )

        if not approve:
            crawlers = {
                submission.developer
                for submission in submissions
                if submission.developer.source_type == MieSourceType.SYSTEM
            }
            for account in crawlers:
                guardrail_service.evaluate_rejection_breaker(
                    account=account, actor=actor
                )

    return submissions


def _decision_summary(approve: bool, title: str) -> str:
    """Activity-log line, with the title trimmed to fit the column."""

    verb = "approved" if approve else "rejected"
    return f"You {verb} the idea '{title[:SUMMARY_TITLE_LIMIT]}'."


def _decision_details(submission: CourseSubmission) -> dict:
    """Audit-row details for a decision, naming the linked course if any.

    `resulting_course_id` appears only when a course is linked, so an idea
    with no course logs exactly the keys it always has.
    """

    details = {
        "submission_id": str(submission.id),
        "reference": submission.public_reference,
    }
    if submission.resulting_course_id:
        details["resulting_course_id"] = str(submission.resulting_course_id)
    return details


def set_payout_bypass(*, actor, submission: CourseSubmission, bypass: bool) -> CourseSubmission:
    """Mark one specific idea as no-payout and tell the developer now.

    Fires SUBMISSION_PAYOUT_BYPASS_UPDATED regardless of status change -
    this is a commercial signal, not a pipeline move, so it gets its own
    event type rather than overloading approve/reject.
    """

    permission_service.require_permission(actor, codenames.MIE_MANAGE_CONSOLE)
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

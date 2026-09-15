import math
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, Min
from django.utils import timezone
from rest_framework import exceptions

from api.mie.enums import MieSourceType, SubmissionStatus, WebhookEventType
from api.mie.models import CourseSubmission, DeveloperAccount, WebhookEvent
from api.mie.models.course_submission import CONFIDENCE_NOTE_MAX_LENGTH
from api.mie.services.dedup_service import DedupOutcome, evaluate_title, normalize_title

DAILY_CAP_WINDOW = timedelta(hours=24)
"""The rolling window the SYSTEM-account submission cap counts over."""

EVENT_TYPE_BY_STATUS = {
    SubmissionStatus.PENDING_REVIEW: WebhookEventType.SUBMISSION_QUEUED,
    SubmissionStatus.DUPLICATE_IN_QUEUE: WebhookEventType.SUBMISSION_DUPLICATE_IN_QUEUE,
    SubmissionStatus.DUPLICATE_EXISTING: WebhookEventType.SUBMISSION_DUPLICATE_EXISTING,
    SubmissionStatus.PREVIOUSLY_REJECTED: WebhookEventType.SUBMISSION_PREVIOUSLY_REJECTED,
    SubmissionStatus.APPROVED: WebhookEventType.SUBMISSION_APPROVED,
    SubmissionStatus.REJECTED: WebhookEventType.SUBMISSION_REJECTED,
}


class DailySubmissionCapReached(Exception):
    """A SYSTEM account has used up its rolling 24-hour allowance.

    `retry_after_seconds` is how long until the oldest counted submission
    leaves the window - the moment the next one will be accepted - so the
    view can hand it straight to the client as Retry-After.
    """

    def __init__(self, retry_after_seconds: int):
        super().__init__(f"Daily submission cap reached; retry in {retry_after_seconds}s.")
        self.retry_after_seconds = retry_after_seconds


def validate_idea_payload(payload: dict) -> str:
    """Extract and validate the title from a raw Endpoint 1 body.

    The body is stored verbatim; only the title is required up front -
    everything else rides along untouched for the review surfaces.
    """

    if not isinstance(payload, dict):
        raise exceptions.ValidationError(
            {"payload": ["Submission body must be a JSON object."]}
        )
    title = payload.get("title")
    if not isinstance(title, str) or not title.strip():
        raise exceptions.ValidationError(
            {"title": ["A non-empty string title is required."]}
        )
    if len(title.strip()) > 255:
        raise exceptions.ValidationError(
            {"title": ["Title must be 255 characters or fewer."]}
        )
    return normalize_title(title)


def submit_idea(*, developer, payload: dict) -> tuple[CourseSubmission, bool]:
    """Ingest one course idea (Endpoint 1).

    Runs the ordered dedup checks, persists the submission in the outcome
    state, records the immediate webhook event for whatever happened -
    including dedup short-circuits - and returns (submission, queued)
    where queued is True only for PENDING_REVIEW outcomes. Dispatch of
    the recorded events is the webhook dispatcher's job.

    SYSTEM accounts are also held to a rolling 24-hour cap: over it,
    nothing is stored and DailySubmissionCapReached is raised. External
    developers never reach that check.
    """

    title = validate_idea_payload(payload)
    confidence_note = _extract_confidence_note(payload)

    if developer.source_type != MieSourceType.SYSTEM:
        submission = _persist_submission(developer, payload, title, confidence_note)
    else:
        # Count and insert share one transaction behind a lock on the
        # account row, so two concurrent crawler requests cannot both read
        # "19 of 20" and both get in.
        with transaction.atomic():
            _enforce_daily_cap(developer)
            submission = _persist_submission(developer, payload, title, confidence_note)

    return submission, submission.status == SubmissionStatus.PENDING_REVIEW


def record_event(submission: CourseSubmission) -> WebhookEvent:
    """Create the outbound event row for a transition.

    The dispatcher (slice 6) signs and sends what is recorded here; the
    row exists from the moment the transition happens so nothing can be
    lost between ingestion and delivery.
    """

    return WebhookEvent.objects.create(
        submission=submission,
        event_type=EVENT_TYPE_BY_STATUS[submission.status],
        payload=_event_payload(submission),
    )


def _persist_submission(developer, payload, title, confidence_note) -> CourseSubmission:
    """Dedup, store and record the event, resolving a lost queue race.

    The inner atomic becomes a savepoint whenever the caller already holds
    a transaction (the SYSTEM cap path does), so the IntegrityError rolls
    back only this attempt and leaves the outer transaction - and the
    account lock it holds - usable for the lost-race insert.
    """

    try:
        with transaction.atomic():
            outcome: DedupOutcome = evaluate_title(title)
            submission = CourseSubmission.objects.create(
                developer=developer,
                payload=payload,
                title=title,
                confidence_note=confidence_note,
                status=outcome.status,
                rejection_reason=outcome.inherited_reason,
            )
            if outcome.status == SubmissionStatus.PENDING_REVIEW:
                CourseSubmission.objects.filter(id=submission.id).update(
                    queued_at=submission.created_datetime
                )
            record_event(submission)
    except IntegrityError:
        # Lost a race against the partial unique index on pending titles;
        # another developer enqueued this exact title mid-transaction.
        submission = _record_lost_race(developer, payload, title, confidence_note)
    return submission


def _enforce_daily_cap(developer) -> None:
    """Refuse a SYSTEM submission once the rolling 24-hour cap is used up.

    Runs inside the caller's transaction. Locking the account row first
    serialises concurrent requests from the same account, so the count
    always sees the previous request's insert. Every row counts whatever
    its outcome: a crawler stuck resubmitting duplicates is exactly what
    the cap exists to stop.
    """

    DeveloperAccount.objects.select_for_update().only("id").get(id=developer.id)

    now = timezone.now()
    usage = CourseSubmission.objects.filter(
        developer=developer, created_datetime__gte=now - DAILY_CAP_WINDOW
    ).aggregate(used=Count("id"), oldest=Min("created_datetime"))
    if usage["used"] < settings.MIE_SYSTEM_DAILY_SUBMISSION_CAP:
        return

    # The next slot opens when the oldest counted row leaves the window. If
    # the cap was lowered below current usage this undershoots, and the
    # retry simply earns a fresh Retry-After.
    frees_at = (usage["oldest"] or now) + DAILY_CAP_WINDOW
    raise DailySubmissionCapReached(max(1, math.ceil((frees_at - now).total_seconds())))


def _extract_confidence_note(payload: dict) -> str:
    """Lift the optional confidence_note out of a raw Endpoint 1 body.

    Checked here for the same reason the title is: the serializer alone
    would coerce a number to a string, leaving the stored payload and the
    extracted column disagreeing about what was sent.
    """

    note = payload.get("confidence_note", "")
    if not isinstance(note, str):
        raise exceptions.ValidationError(
            {"confidence_note": ["confidence_note must be a string."]}
        )
    note = note.strip()
    if len(note) > CONFIDENCE_NOTE_MAX_LENGTH:
        raise exceptions.ValidationError(
            {
                "confidence_note": [
                    f"confidence_note must be {CONFIDENCE_NOTE_MAX_LENGTH} "
                    "characters or fewer."
                ]
            }
        )
    return note


def _record_lost_race(developer, payload, title, confidence_note) -> CourseSubmission:
    """Re-evaluate after an index race and persist without re-checking.

    The unique index guarantees exactly one PENDING_REVIEW row per title,
    so on collision the safe answer is DUPLICATE_IN_QUEUE - re-running
    evaluate could loop under repeated contention.
    """

    with transaction.atomic():
        submission = CourseSubmission.objects.create(
            developer=developer,
            payload=payload,
            title=title,
            confidence_note=confidence_note,
            status=SubmissionStatus.DUPLICATE_IN_QUEUE,
        )
        record_event(submission)
    return submission


def _event_payload(submission: CourseSubmission) -> dict:
    """The JSON body delivered to the developer's webhook endpoint."""

    return {
        "submission": {
            "reference": submission.public_reference,
            "status": submission.status,
            "title": submission.title,
        },
        "developer_email": submission.developer.email,
    }

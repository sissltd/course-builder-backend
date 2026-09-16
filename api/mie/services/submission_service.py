import math
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, Min, Q
from django.utils import timezone
from rest_framework import exceptions

from api.courses.enums import DifficultyLevel
from api.mie.enums import MieSourceType, SubmissionStatus, WebhookEventType
from api.mie.models import CourseSubmission, DeveloperAccount, WebhookEvent
from api.mie.models.course_submission import (
    CONFIDENCE_NOTE_MAX_LENGTH,
    DESCRIPTION_MAX_LENGTH,
)
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

    fields = _extract_fields(payload)

    if developer.source_type != MieSourceType.SYSTEM:
        submission = _persist_submission(developer, payload, fields)
    else:
        # Count and insert share one transaction behind a lock on the
        # account row, so two concurrent crawler requests cannot both read
        # "19 of 20" and both get in.
        with transaction.atomic():
            _enforce_daily_cap(developer)
            submission = _persist_submission(developer, payload, fields)

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


def _persist_submission(developer, payload, fields: dict) -> CourseSubmission:
    """Dedup, store and record the event, resolving a lost queue race.

    The inner atomic becomes a savepoint whenever the caller already holds
    a transaction (the SYSTEM cap path does), so the IntegrityError rolls
    back only this attempt and leaves the outer transaction - and the
    account lock it holds - usable for the lost-race insert.
    """

    try:
        with transaction.atomic():
            outcome: DedupOutcome = evaluate_title(fields["title"])
            submission = CourseSubmission.objects.create(
                developer=developer,
                payload=payload,
                status=outcome.status,
                rejection_reason=outcome.inherited_reason,
                **fields,
            )
            if outcome.status == SubmissionStatus.PENDING_REVIEW:
                CourseSubmission.objects.filter(id=submission.id).update(
                    queued_at=submission.created_datetime
                )
            record_event(submission)
    except IntegrityError:
        # Lost a race against the partial unique index on pending titles;
        # another developer enqueued this exact title mid-transaction.
        submission = _record_lost_race(developer, payload, fields)
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


def _extract_fields(payload: dict) -> dict:
    """Lift the columns the platform reads out of a raw Endpoint 1 body.

    Every one of these also stays verbatim in `payload`; the columns are
    the copies the review screens sort, filter and display on. Types are
    checked here rather than left to the serializer for the reason the
    title is: the serializer would coerce, leaving the stored payload and
    the extracted column disagreeing about what was actually sent.
    """

    return {
        "title": validate_idea_payload(payload),
        "confidence_note": _extract_text(
            payload, "confidence_note", CONFIDENCE_NOTE_MAX_LENGTH
        ),
        "description": _extract_text(payload, "description", DESCRIPTION_MAX_LENGTH),
        "category": _resolve_category(payload.get("category")),
        "difficulty_level": _extract_difficulty_level(payload),
        "searches_per_month": _extract_searches_per_month(payload),
    }


def _extract_text(payload: dict, field: str, max_length: int) -> str:
    """Trimmed text for `field`, or "" when it was not sent."""

    value = payload.get(field, "")
    if not isinstance(value, str):
        raise exceptions.ValidationError({field: [f"{field} must be a string."]})
    value = value.strip()
    if len(value) > max_length:
        raise exceptions.ValidationError(
            {field: [f"{field} must be {max_length} characters or fewer."]}
        )
    return value


def _resolve_category(raw):
    """Map a submitted category name or slug onto a real platform Category.

    Matching is case-insensitive on either, and archived categories are
    ignored - an idea cannot be filed under a retired one. A value that
    matches nothing is not an error: the column stays null and the raw
    string survives in the payload, so a reviewer can still see what the
    submitter meant and set the category themselves.
    """

    from api.catalog.enums import CategoryStatus
    from api.catalog.models import Category

    if raw in (None, ""):
        return None
    if not isinstance(raw, str):
        raise exceptions.ValidationError({"category": ["category must be a string."]})

    candidate = raw.strip()
    return (
        Category.objects.exclude(status=CategoryStatus.ARCHIVED)
        .filter(Q(name__iexact=candidate) | Q(slug__iexact=candidate))
        .first()
    )


def _extract_difficulty_level(payload: dict) -> str:
    """The submitter's claimed difficulty, normalised to the platform enum."""

    raw = payload.get("difficulty_level", "")
    if raw in (None, ""):
        return ""
    if not isinstance(raw, str) or raw.strip().upper() not in DifficultyLevel.values:
        raise exceptions.ValidationError(
            {
                "difficulty_level": [
                    "difficulty_level must be one of: "
                    f"{', '.join(DifficultyLevel.values)}."
                ]
            }
        )
    return raw.strip().upper()


def _extract_searches_per_month(payload: dict):
    """Monthly search volume, or None when it was not sent.

    `bool` is rejected explicitly because it is an int in Python, and
    `True` is not a search volume.
    """

    raw = payload.get("searches_per_month")
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise exceptions.ValidationError(
            {
                "searches_per_month": [
                    "searches_per_month must be a whole number of 0 or more."
                ]
            }
        )
    return raw


def _record_lost_race(developer, payload, fields: dict) -> CourseSubmission:
    """Re-evaluate after an index race and persist without re-checking.

    The unique index guarantees exactly one PENDING_REVIEW row per title,
    so on collision the safe answer is DUPLICATE_IN_QUEUE - re-running
    evaluate could loop under repeated contention.
    """

    with transaction.atomic():
        submission = CourseSubmission.objects.create(
            developer=developer,
            payload=payload,
            status=SubmissionStatus.DUPLICATE_IN_QUEUE,
            **fields,
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

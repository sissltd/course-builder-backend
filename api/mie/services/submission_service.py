from django.db import IntegrityError, transaction
from rest_framework import exceptions

from api.mie.enums import SubmissionStatus, WebhookEventType
from api.mie.models import CourseSubmission, WebhookEvent
from api.mie.services.dedup_service import DedupOutcome, evaluate_title, normalize_title

EVENT_TYPE_BY_STATUS = {
    SubmissionStatus.PENDING_REVIEW: WebhookEventType.SUBMISSION_QUEUED,
    SubmissionStatus.DUPLICATE_IN_QUEUE: WebhookEventType.SUBMISSION_DUPLICATE_IN_QUEUE,
    SubmissionStatus.DUPLICATE_EXISTING: WebhookEventType.SUBMISSION_DUPLICATE_EXISTING,
    SubmissionStatus.PREVIOUSLY_REJECTED: WebhookEventType.SUBMISSION_PREVIOUSLY_REJECTED,
    SubmissionStatus.APPROVED: WebhookEventType.SUBMISSION_APPROVED,
    SubmissionStatus.REJECTED: WebhookEventType.SUBMISSION_REJECTED,
}


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
    """

    title = validate_idea_payload(payload)

    try:
        with transaction.atomic():
            outcome: DedupOutcome = evaluate_title(title)
            submission = CourseSubmission.objects.create(
                developer=developer,
                payload=payload,
                title=title,
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
        submission = _record_lost_race(developer, payload, title)

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


def _record_lost_race(developer, payload, title) -> CourseSubmission:
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

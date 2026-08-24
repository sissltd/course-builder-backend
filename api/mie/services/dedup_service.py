"""The three sequential dedup checks applied to every incoming title.

Order matters and mirrors the Studio API contract: the first matching
check decides the outcome and the rest are skipped -

1. previously rejected  -> PREVIOUSLY_REJECTED (this exact title was
   rejected before; the new row inherits the old rejection reason)
2. existing course      -> DUPLICATE_EXISTING (a platform course with
   this title already exists)
3. active queue         -> DUPLICATE_IN_QUEUE (the same title is already
   awaiting review)

Titles are compared case-insensitively after trimming; submissions store
the trimmed form so lookups stay exact.
"""

from dataclasses import dataclass

from api.mie.enums import SubmissionStatus


@dataclass(frozen=True)
class DedupOutcome:
    status: SubmissionStatus
    inherited_reason: object = None


def normalize_title(raw: str) -> str:
    """Canonical stored/comparison form of a submitted title."""

    return raw.strip()


def evaluate_title(title: str) -> DedupOutcome:
    """Run the ordered checks; return the resulting DedupOutcome."""

    from api.courses.models import Course
    from api.mie.models import CourseSubmission

    candidate = normalize_title(title)

    prior_rejection = (
        CourseSubmission.objects.filter(
            status=SubmissionStatus.REJECTED, title__iexact=candidate
        )
        .select_related("rejection_reason")
        .order_by("-decided_at")
        .first()
    )
    if prior_rejection is not None:
        return DedupOutcome(
            SubmissionStatus.PREVIOUSLY_REJECTED,
            inherited_reason=prior_rejection.rejection_reason,
        )

    if Course.objects.filter(title__iexact=candidate).exists():
        return DedupOutcome(SubmissionStatus.DUPLICATE_EXISTING)

    queued_match = (
        CourseSubmission.objects.filter(
            status=SubmissionStatus.PENDING_REVIEW, title__iexact=candidate
        )
        .exists()
    )
    if queued_match:
        return DedupOutcome(SubmissionStatus.DUPLICATE_IN_QUEUE)

    return DedupOutcome(SubmissionStatus.PENDING_REVIEW)

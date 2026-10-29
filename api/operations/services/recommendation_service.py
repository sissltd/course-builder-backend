"""MIE recommendation feed for the admin Recommendations screen.

Ranks the pending idea queue by the market-intelligence signals admins
record on it (`demand_score`, then `estimated_monthly_earnings`). Filtering,
pagination and the decisions themselves belong to the view; the ranking and
the coverage counts live here.
"""

from django.db.models import F, QuerySet

from api.mie.enums import SubmissionStatus
from api.mie.models import CourseSubmission


def recommendation_queryset() -> QuerySet[CourseSubmission]:
    """Ideas still awaiting a decision, best first.

    Only PENDING_REVIEW rows are recommendable - an approved or
    deduplicated idea is not something an admin can still act on.
    `select_related` covers every relation the row serializer reads, so a
    page costs the same number of queries whatever its size.
    """

    return (
        CourseSubmission.objects.filter(status=SubmissionStatus.PENDING_REVIEW)
        .select_related("developer", "category")
        .order_by(
            # nulls_last is load-bearing: Postgres sorts NULLs FIRST under
            # DESC, which would put every unscored idea at the top of a
            # screen whose whole purpose is ranking by score.
            F("demand_score").desc(nulls_last=True),
            F("estimated_monthly_earnings").desc(nulls_last=True),
            F("created_datetime").desc(),
        )
    )


def recommendation_totals(queryset: QuerySet[CourseSubmission]) -> dict:
    """Scoring coverage for the rows the caller is actually looking at.

    Counted over the filtered queryset rather than the whole queue, so the
    two numbers describe the current view: with a category selected they
    answer "how much of *this* is scored", which is what the screen shows.
    """

    return {
        "pending_total": queryset.count(),
        "scored_total": queryset.exclude(demand_score__isnull=True).count(),
    }

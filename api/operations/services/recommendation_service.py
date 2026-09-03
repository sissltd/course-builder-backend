"""MIE recommendation feed for the admin dashboard.

Reads the market-intelligence signals admins record on MIE submissions
(`demand_score`, `estimated_monthly_earnings`) and ranks the pending
queue by them. Read-only: nothing here writes to the MIE app, and the
partner-facing MIE contract is untouched.
"""

from django.db.models import F

from api.mie.enums import SubmissionStatus
from api.mie.models import CourseSubmission

DEFAULT_LIMIT = 20
MAX_LIMIT = 100


def get_recommendations(*, limit: int = DEFAULT_LIMIT) -> dict:
    """Highest-demand ideas still awaiting a decision.

    Only PENDING_REVIEW rows are recommendable - an approved or
    deduplicated idea is not something an admin can still act on. Rows
    without a demand score sort last rather than being hidden, so an
    unscored backlog is visible instead of silently absent.
    """

    limit = max(1, min(limit, MAX_LIMIT))

    queryset = (
        CourseSubmission.objects.filter(status=SubmissionStatus.PENDING_REVIEW)
        .select_related("developer")
        .order_by(
            # nulls_last is load-bearing: Postgres sorts NULLs FIRST under
            # DESC, which would put every unscored idea at the top of a
            # screen whose whole purpose is ranking by score.
            F("demand_score").desc(nulls_last=True),
            F("estimated_monthly_earnings").desc(nulls_last=True),
            F("created_datetime").desc(),
        )
    )

    rows = [
        {
            "id": str(row.id),
            "reference": row.public_reference,
            "title": row.title,
            "developer_email": row.developer.email,
            "demand_score": row.demand_score,
            "estimated_monthly_earnings": (
                str(row.estimated_monthly_earnings)
                if row.estimated_monthly_earnings is not None
                else None
            ),
            "submitted_at": row.created_datetime.isoformat(),
        }
        for row in queryset[:limit]
    ]

    return {
        "pending_total": queryset.count(),
        "scored_total": queryset.exclude(demand_score__isnull=True).count(),
        "results": rows,
    }

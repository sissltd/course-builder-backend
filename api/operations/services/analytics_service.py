"""Admin analytics aggregation.

Every figure here is measured from rows that exist. Where a metric has no
data behind it yet the value is null rather than zero, and the tile can
say "no data" instead of implying a real reading of nothing - the
distinction matters on a screen people make spending decisions from.
"""

from datetime import timedelta
from decimal import Decimal

from django.db.models import Avg, Count, F, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from api.courses.enums import CourseStatus, DistributionChannel
from api.courses.models import Course, CourseDistribution
from api.operations.enums import EnrollmentStatus
from api.operations.models import Enrollment, ProductionCost
from api.reviews.enums import ReviewActionType
from api.reviews.models import ReviewAction
from api.wallet.models import Wallet

PERIODS = {"24h": 1, "7d": 7, "31d": 31, "6m": 182}
DEFAULT_PERIOD = "7d"

KPI_TARGETS = {
    "daily_output": "200+",
    "first_pass_approval_percent": "\u2265 80%",
    "avg_pipeline_time_minutes": "> 60m",
    "cost_per_course": "> $5.00",
    "review_turnaround_hours": "48hr",
    "system_uptime_percent": "99.9%",
}
"""Targets shown beside each KPI. Served with the figures so the client
does not hardcode business goals it cannot see change."""


def get_analytics(*, period: str = DEFAULT_PERIOD) -> dict:
    """The Analytics screen: headline tiles, distribution, trend and KPIs."""

    if period not in PERIODS:
        period = DEFAULT_PERIOD
    since = timezone.now() - timedelta(days=PERIODS[period])

    return {
        "period": period,
        "since": since.isoformat(),
        "catalog": _catalog(since),
        "enrollment": _enrollment(since),
        "cost": _cost(since),
        "earnings": _earnings(),
        "distribution": _distribution(),
        "production_vs_approval": _production_vs_approval(since),
        "kpis": _kpis(since),
    }


def _catalog(since) -> dict:
    return {
        "total_catalog": Course.objects.count(),
        "published": Course.objects.filter(status=CourseStatus.PUBLISHED).count(),
        "created_in_period": Course.objects.filter(
            created_datetime__gte=since
        ).count(),
    }


def _enrollment(since) -> dict:
    """Enrolment volume and mean completion.

    `avg_completion_rate` is null with no enrolments rather than 0.0 - a
    catalogue nobody has enrolled on has no completion rate, and showing
    zero would read as "everyone drops out".
    """

    total = Enrollment.objects.count()
    aggregate = Enrollment.objects.aggregate(mean=Avg("progress_percent"))
    return {
        "total_enrollment": total,
        "enrolled_in_period": Enrollment.objects.filter(
            enrolled_at__gte=since
        ).count(),
        "completed": Enrollment.objects.filter(
            status=EnrollmentStatus.COMPLETED
        ).count(),
        "avg_completion_rate": (
            round(float(aggregate["mean"]), 2) if aggregate["mean"] is not None else None
        ),
    }


def _cost(since) -> dict:
    """Spend totals plus the daily series behind the production-cost chart."""

    overall = ProductionCost.objects.aggregate(total=Sum("amount"))["total"]
    in_period = ProductionCost.objects.filter(incurred_at__gte=since).aggregate(
        total=Sum("amount")
    )["total"]

    produced = Course.objects.filter(status=CourseStatus.PUBLISHED).count()

    daily = [
        {"date": row["day"].isoformat(), "amount": str(row["total"])}
        for row in (
            ProductionCost.objects.filter(incurred_at__gte=since)
            .annotate(day=TruncDate("incurred_at"))
            .values("day")
            .annotate(total=Sum("amount"))
            .order_by("day")
        )
    ]

    by_category = [
        {"category": row["category"], "amount": str(row["total"])}
        for row in (
            ProductionCost.objects.filter(incurred_at__gte=since)
            .values("category")
            .annotate(total=Sum("amount"))
            .order_by("-total")
        )
    ]

    return {
        "overall_cost": str(overall) if overall is not None else None,
        "cost_in_period": str(in_period) if in_period is not None else None,
        "cost_per_course": (
            str((overall / produced).quantize(Decimal("0.01")))
            if overall is not None and produced
            else None
        ),
        "daily": daily,
        "by_category": by_category,
    }


def _earnings() -> dict:
    """Platform-wide creator earnings held in wallets."""

    total = Wallet.objects.aggregate(total=Sum("balance"))["total"]
    return {"total_earnings": str(total) if total is not None else None}


def _distribution() -> list:
    """Published course counts per sales channel, every channel present."""

    counted = {
        row["channel"]: row["count"]
        for row in (
            CourseDistribution.objects.values("channel").annotate(count=Count("id"))
        )
    }
    return [
        {
            "channel": channel.value,
            "label": channel.label,
            "count": counted.get(channel.value, 0),
        }
        for channel in DistributionChannel
    ]


def _production_vs_approval(since) -> dict:
    """Produced / approved / rejected counts for the comparison chart."""

    decisions = {
        row["action"]: row["count"]
        for row in (
            ReviewAction.objects.filter(created_datetime__gte=since)
            .values("action")
            .annotate(count=Count("id"))
        )
    }
    return {
        "produced": Course.objects.filter(created_datetime__gte=since).count(),
        "approved": decisions.get(ReviewActionType.APPROVE.value, 0),
        "rejected": decisions.get(ReviewActionType.REJECT.value, 0),
    }


def _kpis(since) -> dict:
    """Operational KPIs, each null when nothing backs it yet."""

    decided = ReviewAction.objects.filter(created_datetime__gte=since)
    total_decisions = decided.count()
    approvals = decided.filter(action=ReviewActionType.APPROVE).count()

    days = max((timezone.now() - since).days, 1)
    produced_in_period = Course.objects.filter(created_datetime__gte=since).count()

    # Imported here so analytics stays a read-only consumer of the other
    # two dashboards rather than duplicating their aggregation logic.
    from api.operations.services import health_service, pipeline_service

    pipeline_seconds = pipeline_service.get_pipeline_overview()[
        "avg_pipeline_seconds"
    ]
    uptime = health_service.get_system_health()["overall_uptime_percent"]

    return {
        "daily_output": round(produced_in_period / days, 2),
        "first_pass_approval_percent": (
            round((approvals / total_decisions) * 100, 2) if total_decisions else None
        ),
        "avg_pipeline_time_minutes": (
            round(pipeline_seconds / 60, 2) if pipeline_seconds is not None else None
        ),
        "cost_per_course": _cost(since)["cost_per_course"],
        "review_turnaround_hours": _review_turnaround_hours(since),
        "system_uptime_percent": uptime,
        "targets": dict(KPI_TARGETS),
    }


def _review_turnaround_hours(since):
    """Mean hours from submission to first decision, or None if none yet."""

    mean = (
        ReviewAction.objects.filter(
            created_datetime__gte=since, course__submitted_at__isnull=False
        )
        .annotate(turnaround=F("created_datetime") - F("course__submitted_at"))
        .aggregate(mean=Avg("turnaround"))["mean"]
    )
    return round(mean.total_seconds() / 3600, 2) if mean is not None else None

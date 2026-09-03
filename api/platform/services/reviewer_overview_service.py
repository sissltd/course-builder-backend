from datetime import timedelta

from django.db.models import Count
from django.db.models.functions import TruncDate
from django.utils import timezone

from api.courses.enums import AppealStatus, CourseStatus
from api.courses.models import Course, CourseAppeal
from api.reviews.enums import ReviewActionType
from api.reviews.models import ReviewAction
from api.users.permissions import IsCreatorReviewerRole, require_role

ACTIVITY_PERIODS = ("all_time", "today", "this_week", "this_month")
"""Ranges offered by the dashboard's Activity Overview dropdown."""

DEFAULT_ACTIVITY_PERIOD = "today"


def get_overview(*, actor) -> dict:
    """Aggregate the counts a reviewer's home screen leads with.

    Mirrors admin_overview_service.get_overview: counts rather than lists,
    zero-filled status keys for stable tiles. The queue block tells the
    reviewer what is waiting; the my_decisions block shows their own
    lifetime approve/reject split plus today's volume.
    """

    require_role(actor, IsCreatorReviewerRole.allowed_roles)

    queue_statuses = (CourseStatus.SUBMITTED, CourseStatus.IN_REVIEW)
    counted = {
        row["status"]: row["count"]
        for row in (
            Course.objects.filter(status__in=queue_statuses)
            .values("status")
            .annotate(count=Count("id"))
        )
    }
    queue = {status.value: counted.get(status.value, 0) for status in queue_statuses}

    decisions = {
        row["action"]: row["count"]
        for row in (
            ReviewAction.objects.filter(reviewer=actor)
            .values("action")
            .annotate(count=Count("id"))
        )
    }
    my_decisions = {
        "approved": decisions.get(ReviewActionType.APPROVE.value, 0),
        "today": _count_actions_today(actor),
    }

    return {
        "queue": queue,
        "my_decisions": my_decisions,
        # The three tiles the dashboard leads with. `queue`/`my_decisions`
        # above are kept so existing clients do not break.
        "courses_reviewed": sum(decisions.values()),
        "courses_in_queue": sum(queue.values()),
        "escalations_resolved": _count_escalations_resolved(actor),
    }


def _count_escalations_resolved(reviewer) -> int:
    """Appeals this reviewer has decided.

    An appeal is the platform's escalation path - PRD Section 12 routes a
    disputed rejection to a senior reviewer - so "escalations resolved" is
    the count of appeals this reviewer took a decision on.
    """

    return CourseAppeal.objects.filter(
        reviewed_by=reviewer, status__in=(AppealStatus.APPROVED, AppealStatus.REJECTED)
    ).count()


def get_activity_overview(*, actor, period: str = DEFAULT_ACTIVITY_PERIOD) -> dict:
    """Daily Escalated / Approved / Rejected counts for the activity chart.

    Every day in the range gets an entry, including zeroes, so the chart
    has a stable x-axis and the client never has to fill gaps itself.
    `all_time` starts at the reviewer's first recorded activity; with no
    activity at all it degrades to today alone rather than an empty chart.
    """

    require_role(actor, IsCreatorReviewerRole.allowed_roles)

    if period not in ACTIVITY_PERIODS:
        period = DEFAULT_ACTIVITY_PERIOD

    start, end = _resolve_range(actor=actor, period=period)

    approvals = _daily_counts(
        ReviewAction.objects.filter(
            reviewer=actor, action=ReviewActionType.APPROVE
        ),
        start,
    )
    rejections = _daily_counts(
        ReviewAction.objects.filter(reviewer=actor, action=ReviewActionType.REJECT),
        start,
    )
    escalations = _daily_counts(
        CourseAppeal.objects.filter(
            reviewed_by=actor,
            status__in=(AppealStatus.APPROVED, AppealStatus.REJECTED),
        ),
        start,
    )

    series = []
    day = start
    while day <= end:
        series.append(
            {
                "date": day.isoformat(),
                "escalated": escalations.get(day, 0),
                "approved": approvals.get(day, 0),
                "rejected": rejections.get(day, 0),
            }
        )
        day += timedelta(days=1)

    return {
        "period": period,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "totals": {
            "escalated": sum(row["escalated"] for row in series),
            "approved": sum(row["approved"] for row in series),
            "rejected": sum(row["rejected"] for row in series),
        },
        "series": series,
    }


def _resolve_range(*, actor, period: str):
    """Inclusive (start, end) local dates for `period`."""

    today = timezone.localdate()
    if period == "today":
        return today, today
    if period == "this_week":
        return today - timedelta(days=today.weekday()), today
    if period == "this_month":
        return today.replace(day=1), today

    # all_time: from the reviewer's first activity, or today if they have none.
    first_action = (
        ReviewAction.objects.filter(reviewer=actor)
        .order_by("created_datetime")
        .values_list("created_datetime", flat=True)
        .first()
    )
    first_appeal = (
        CourseAppeal.objects.filter(reviewed_by=actor)
        .order_by("created_datetime")
        .values_list("created_datetime", flat=True)
        .first()
    )
    stamps = [s for s in (first_action, first_appeal) if s is not None]
    if not stamps:
        return today, today
    return timezone.localtime(min(stamps)).date(), today


def _daily_counts(queryset, start) -> dict:
    """{date: count} for `queryset`, from `start` onwards."""

    return {
        row["day"]: row["count"]
        for row in (
            queryset.annotate(day=TruncDate("created_datetime"))
            .filter(day__gte=start)
            .values("day")
            .annotate(count=Count("id"))
        )
    }


def _count_actions_today(reviewer) -> int:
    """How many decisions `reviewer` has recorded since local midnight."""

    today = timezone.localdate()
    return (
        ReviewAction.objects.filter(reviewer=reviewer)
        .annotate(day=TruncDate("created_datetime"))
        .filter(day=today)
        .count()
    )

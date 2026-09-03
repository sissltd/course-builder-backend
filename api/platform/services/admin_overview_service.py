from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from api.courses.enums import CourseStatus
from api.courses.models import Course
from api.operations.models import ProductionCost
from api.payments.models.transaction_model import Transaction
from api.users.enums import AccountStatus, KYCStatus
from api.users.models import KYCVerification, User
from api.users.permissions import IsAdminOrSuperAdminRole, require_role
from api.wallet.enums import (
    TransactionStatus,
    TransactionType,
    WithdrawalRequestStatus,
)
from api.wallet.models import Wallet, WithdrawalRequest


#: Trend window per period selector value, matching the design's
#: 24hrs / 7days / 31days / 6months control.
TREND_DAYS = {"24h": 1, "7d": 7, "31d": 31, "6m": 182}
DEFAULT_PERIOD = "7d"


def get_overview(*, actor: User, period: str = DEFAULT_PERIOD) -> dict:
    """Aggregate the counts an admin home screen leads with.

    Deliberately counts rather than lists: every figure here has a dedicated
    endpoint behind it (the review queue, the KYC queue, the payout worklist),
    so this exists to tell an admin *where to look*, not to duplicate those
    payloads.

    Each block is one grouped query rather than one query per status, so the
    whole overview costs a fixed handful of queries no matter how many statuses
    the enums grow.
    """

    require_role(actor, IsAdminOrSuperAdminRole.allowed_roles)

    if period not in TREND_DAYS:
        period = DEFAULT_PERIOD
    days = TREND_DAYS[period]

    return {
        "period": period,
        "users": _counts_by(User.objects.all(), "status", AccountStatus),
        "courses": _counts_by(Course.objects.all(), "status", CourseStatus),
        "kyc": _counts_by(KYCVerification.objects.all(), "status", KYCStatus),
        "withdrawals": _counts_by(
            WithdrawalRequest.objects.all(), "status", WithdrawalRequestStatus
        ),
        "wallet_totals": _wallet_totals(),
        # The design's headline tiles and the two charts beside them. The
        # blocks above are the same data broken down and stay for clients
        # already reading them.
        "today": _today_tiles(),
        "production_trend": _production_trend(days),
        "cost_trend": _cost_trend(days),
    }


def _today_tiles() -> dict:
    """The four tiles across the top of the admin dashboard.

    Each carries the comparison the design shows beside it, so the client
    renders a delta rather than recomputing one from a second call.
    Deltas are null when there is no prior period to compare against -
    "no baseline" is not the same as "no change".
    """

    today = timezone.localdate()
    yesterday = today - timedelta(days=1)

    created_today = _courses_created_on(today)
    created_yesterday = _courses_created_on(yesterday)

    published_24h = Course.objects.filter(
        status=CourseStatus.PUBLISHED,
        updated_datetime__gte=timezone.now() - timedelta(hours=24),
    ).count()

    cost_today = _cost_between(today, today)
    cost_yesterday = _cost_between(yesterday, yesterday)
    published_total = Course.objects.filter(status=CourseStatus.PUBLISHED).count()
    lifetime_cost = ProductionCost.objects.aggregate(total=Sum("amount"))["total"]

    return {
        "courses_created_today": created_today,
        "courses_created_change_percent": _percent_change(
            created_today, created_yesterday
        ),
        "published_last_24h": published_24h,
        "published_total": published_total,
        "daily_cost": str(cost_today) if cost_today is not None else None,
        "daily_cost_change_percent": _percent_change(cost_today, cost_yesterday),
        "avg_cost_per_course": (
            str((lifetime_cost / published_total).quantize(Decimal("0.01")))
            if lifetime_cost is not None and published_total
            else None
        ),
    }


def _courses_created_on(day) -> int:
    return (
        Course.objects.annotate(created_day=TruncDate("created_datetime"))
        .filter(created_day=day)
        .count()
    )


def _cost_between(start, end):
    """Total spend over an inclusive local-date range, or None if none."""

    return ProductionCost.objects.annotate(
        day=TruncDate("incurred_at")
    ).filter(day__gte=start, day__lte=end).aggregate(total=Sum("amount"))["total"]


def _percent_change(current, previous):
    """Percentage movement, or None when there is no baseline.

    Returning 0 for "nothing yesterday" would claim flat performance where
    in fact nothing is comparable.
    """

    if previous in (None, 0):
        return None
    if current is None:
        current = 0
    return round(((Decimal(current) - Decimal(previous)) / Decimal(previous)) * 100, 2)


def _production_trend(days: int = 7) -> list:
    """Courses created per day, zero-filled, for the Production Trend bars."""

    start = timezone.localdate() - timedelta(days=days - 1)
    counted = {
        row["day"]: row["count"]
        for row in (
            Course.objects.annotate(day=TruncDate("created_datetime"))
            .filter(day__gte=start)
            .values("day")
            .annotate(count=Count("id"))
        )
    }
    return _zero_filled(start, days, lambda day: {"count": counted.get(day, 0)})


def _cost_trend(days: int = 7) -> list:
    """Spend per day, zero-filled, for the Average production cost line."""

    start = timezone.localdate() - timedelta(days=days - 1)
    totals = {
        row["day"]: row["total"]
        for row in (
            ProductionCost.objects.annotate(day=TruncDate("incurred_at"))
            .filter(day__gte=start)
            .values("day")
            .annotate(total=Sum("amount"))
        )
    }
    return _zero_filled(
        start, days, lambda day: {"amount": str(totals.get(day, Decimal("0")))}
    )


def _zero_filled(start, days: int, build) -> list:
    """One entry per day from `start`, so charts keep a stable x-axis."""

    series = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        series.append({"date": day.isoformat(), **build(day)})
    return series


def _counts_by(queryset, field: str, choices) -> dict:
    """Count `queryset` grouped by `field`, with every choice present.

    Zero-count statuses are filled in from the enum so the frontend can render
    a stable set of tiles - a status simply vanishing from the payload when
    nothing is in it would make the dashboard's shape depend on the data.
    """

    counted = {
        row[field]: row["count"]
        for row in queryset.values(field).annotate(count=Count("id"))
    }
    return {value: counted.get(value, 0) for value in choices.values}


def _wallet_totals() -> dict:
    """Platform-wide money figures: what is held, earned, and awaiting payout."""

    held = Wallet.objects.aggregate(total=Sum("balance"))["total"]
    totals = Transaction.objects.aggregate(
        credited=Sum(
            "amount",
            filter=Q(type=TransactionType.CREDIT, status=TransactionStatus.COMPLETED),
        ),
        awaiting_payout=Sum(
            "amount",
            filter=Q(type=TransactionType.DEBIT, status=TransactionStatus.PENDING),
        ),
    )
    return {
        "balance_held": held or Decimal("0.00"),
        "total_credited": totals["credited"] or Decimal("0.00"),
        "awaiting_payout": totals["awaiting_payout"] or Decimal("0.00"),
    }

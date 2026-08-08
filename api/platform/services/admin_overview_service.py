from decimal import Decimal

from django.db.models import Count, Q, Sum

from api.courses.enums import CourseStatus
from api.courses.models import Course
from api.users.enums import AccountStatus, KYCStatus
from api.users.models import KYCVerification, User
from api.users.permissions import IsAdminOrSuperAdminRole, require_role
from api.wallet.enums import (
    TransactionStatus,
    TransactionType,
    WithdrawalRequestStatus,
)
from api.wallet.models import Transaction, Wallet, WithdrawalRequest


def get_overview(*, actor: User) -> dict:
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

    return {
        "users": _counts_by(User.objects.all(), "status", AccountStatus),
        "courses": _counts_by(Course.objects.all(), "status", CourseStatus),
        "kyc": _counts_by(KYCVerification.objects.all(), "status", KYCStatus),
        "withdrawals": _counts_by(
            WithdrawalRequest.objects.all(), "status", WithdrawalRequestStatus
        ),
        "wallet_totals": _wallet_totals(),
    }


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

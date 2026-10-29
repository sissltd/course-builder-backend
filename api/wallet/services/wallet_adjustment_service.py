"""Admin wallet adjustments - the "Issue Refund" permission.

Learners never pay on this platform; money only moves to creators. So a
refund here is a correction to a creator's wallet: a credit (a missed or
short payout) or a debit (a payout clawed back), always with a reason.

Every adjustment is double-entry against the `adjustments` internal account,
so the ledger still balances; the internal account absorbs the difference.
A wallet can never be driven below zero - `create_transaction` refuses it
under the row lock. Retrying with the same idempotency key returns the first
result instead of moving money twice.
"""

from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import QuerySet
from rest_framework import exceptions, status
from rest_framework.exceptions import APIException

from api.authentication.services import activity_service
from api.authorization import codenames
from api.authorization.services import permission_service
from api.notification.models import Notification
from api.payments.models.ledgeraccount_models import InternalAccount
from api.payments.services.transaction_services import (
    InsufficientFundsError,
    internal_transfer,
)
from api.users.enums import UserActivityActionEnums, UserActivityCategoryEnums
from api.users.models import User
from api.users.workflow import EARNING_ROLES
from api.wallet.enums import AdjustmentDirection
from api.wallet.models import Wallet, WalletAdjustment

ADJUSTMENTS_ACCOUNT_CODE = "adjustments"


class AdjustmentConflict(APIException):
    """The idempotency key was already used for a different adjustment."""

    status_code = status.HTTP_409_CONFLICT
    default_code = "adjustment_conflict"
    default_detail = "This idempotency key was already used for a different adjustment."


def adjust_wallet(
    *,
    actor: User,
    wallet: Wallet,
    direction: str,
    amount: Decimal,
    reason: str,
    idempotency_key: str,
    request=None,
) -> tuple[WalletAdjustment, bool]:
    """Credit or debit `wallet`. Returns (adjustment, created)."""

    permission_service.require_permission(actor, codenames.CREATORS_ISSUE_REFUND)
    existing = WalletAdjustment.objects.filter(idempotency_key=idempotency_key).first()
    if existing is not None:
        return _replay(
            existing, wallet=wallet, direction=direction, amount=amount
        ), False

    user = wallet.user
    if user.id == actor.id:
        raise exceptions.ValidationError("You cannot adjust your own wallet.")
    if user.role not in EARNING_ROLES:
        raise exceptions.NotFound("Wallet not found.")

    adjustments = InternalAccount.objects.filter(
        code_name=ADJUSTMENTS_ACCOUNT_CODE
    ).first()
    if adjustments is None:
        raise exceptions.APIException(
            "The adjustments ledger account is not configured."
        )

    reference = f"ADJ-{idempotency_key}"
    try:
        with transaction.atomic():
            # Same lock order as confirm_withdrawal (internal account, then
            # wallet), so the two can never deadlock each other.
            internal = InternalAccount.objects.select_for_update().get(
                pk=adjustments.pk
            )
            locked_wallet = Wallet.objects.select_for_update().get(pk=wallet.pk)
            source, destination = (
                (internal, locked_wallet)
                if direction == AdjustmentDirection.CREDIT
                else (locked_wallet, internal)
            )
            internal_transfer(
                amount=amount,
                from_ledger=source,
                to_ledger=destination,
                reference=reference,
                description=f"Admin adjustment: {reason}",
            )
            adjustment = WalletAdjustment.objects.create(
                wallet=locked_wallet,
                user=user,
                direction=direction,
                amount=amount,
                reason=reason,
                reference=reference,
                idempotency_key=idempotency_key,
                created_by=actor,
                updated_by=actor,
            )
            verb = "credited" if direction == AdjustmentDirection.CREDIT else "debited"
            activity_service.log_activity(
                user=user,
                actor_user=actor,
                category=UserActivityCategoryEnums.WALLET,
                action=UserActivityActionEnums.WALLET_ADJUSTED,
                summary=f"Your wallet was {verb} {amount} by an administrator.",
                details={
                    "adjustment_id": str(adjustment.id),
                    "direction": direction,
                    "amount": str(amount),
                    "reason": reason,
                },
                target=adjustment,
                request=request,
            )
            Notification.emit_in_app_notification(
                receivers=[user],
                title="Your wallet was adjusted",
                content=f"Your wallet was {verb} {amount}. Reason: {reason}",
                metadata={"adjustment_id": adjustment.id, "direction": direction},
                critical=True,
            )
    except InsufficientFundsError as exc:
        raise exceptions.ValidationError(
            {"amount": "The wallet does not hold enough to debit this amount."}
        ) from exc
    except IntegrityError:
        # A concurrent request with the same key won the race.
        existing = WalletAdjustment.objects.get(idempotency_key=idempotency_key)
        return _replay(
            existing, wallet=wallet, direction=direction, amount=amount
        ), False
    return adjustment, True


def list_adjustments(
    *, actor: User, user_id=None, direction=None
) -> QuerySet[WalletAdjustment]:
    permission_service.require_any_permission(
        actor, (codenames.CREATORS_VIEW_WALLET, codenames.CREATORS_ISSUE_REFUND)
    )
    adjustments = WalletAdjustment.objects.select_related(
        "user", "created_by"
    ).order_by("-created_datetime")
    if user_id is not None:
        adjustments = adjustments.filter(user_id=user_id)
    if direction:
        adjustments = adjustments.filter(direction=direction)
    return adjustments


def _replay(
    existing: WalletAdjustment, *, wallet, direction, amount
) -> WalletAdjustment:
    if (existing.wallet_id, existing.direction, existing.amount) != (
        wallet.id,
        direction,
        Decimal(amount),
    ):
        raise AdjustmentConflict()
    return existing

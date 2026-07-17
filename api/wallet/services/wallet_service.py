from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import QuerySet
from rest_framework import exceptions

from api.courses.models import Course
from api.users.models import User
from api.wallet.enums import TransactionStatus, TransactionType
from api.wallet.models import Transaction, Wallet


def get_or_create_wallet(*, user: User) -> Wallet:
    """Lazily provision a zero-balance wallet for `user` on first access.

    No post_save signal on User is used - wallets are created on demand so no
    wallet row exists until a user actually needs one (first credit or first
    wallet-detail view).
    """

    wallet, _created = Wallet.objects.get_or_create(user=user)
    return wallet


def credit_wallet(
    *, user: User, amount: Decimal, course: Course | None = None, description: str = ""
) -> Transaction:
    """Credit `amount` to `user`'s wallet, creating a COMPLETED Transaction.

    Uses select_for_update() inside an atomic transaction so concurrent
    credits to the same wallet cannot produce a lost update. Does not send a
    notification itself - callers (e.g. review_service.approve_course) are
    responsible for that, avoiding duplicate notifications when this is one
    step of a larger workflow.
    """

    with transaction.atomic():
        wallet = get_or_create_wallet(user=user)
        wallet = Wallet.objects.select_for_update().get(pk=wallet.pk)

        txn = Transaction.objects.create(
            wallet=wallet,
            course=course,
            amount=amount,
            type=TransactionType.CREDIT,
            status=TransactionStatus.COMPLETED,
            description=description,
        )
        wallet.balance = wallet.balance + amount
        wallet.save(update_fields=["balance", "updated_datetime"])

    return txn


def create_withdrawal_request(*, user: User, amount: Decimal) -> Transaction:
    """Create a PENDING withdrawal request and reserve the funds immediately by
    decrementing the wallet balance.

    Raises ValidationError if amount is below settings.MINIMUM_WITHDRAWAL_THRESHOLD
    or exceeds the current balance (balance must never go negative). Actual
    payout/settlement via a payment gateway is deferred - this Transaction
    stays PENDING for manual/future processing.
    """

    if amount < settings.MINIMUM_WITHDRAWAL_THRESHOLD:
        raise exceptions.ValidationError(
            f"Minimum withdrawal amount is {settings.MINIMUM_WITHDRAWAL_THRESHOLD}."
        )

    with transaction.atomic():
        wallet = get_or_create_wallet(user=user)
        wallet = Wallet.objects.select_for_update().get(pk=wallet.pk)

        if amount > wallet.balance:
            raise exceptions.ValidationError(
                "Withdrawal amount exceeds available balance."
            )

        txn = Transaction.objects.create(
            wallet=wallet,
            amount=amount,
            type=TransactionType.DEBIT,
            status=TransactionStatus.PENDING,
            description="Withdrawal request",
        )
        wallet.balance = wallet.balance - amount
        wallet.save(update_fields=["balance", "updated_datetime"])

    return txn


def list_transactions(*, user: User) -> QuerySet[Transaction]:
    """Return the transaction history for `user`'s wallet, newest first."""

    return Transaction.objects.filter(wallet__user=user)

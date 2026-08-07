from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Q, QuerySet, Sum
from django.utils import timezone
from rest_framework import exceptions

from api.authentication.enums import TokenPurpose
from api.authentication.services import token_service
from api.courses.models import Course
from api.notification.models import Notification
from api.payments.models.bankaccount_models import BankAccount
from api.payments.models.transaction_model import Transaction
from api.platform.services import platform_settings_service
from api.users.models import User
from api.users.permissions import IsCourseCreatorRole, require_role
from api.users.services import kyc_service
from api.wallet.enums import (
    TransactionStatus,
    TransactionType,
    WithdrawalRequestStatus,
)
from api.wallet.models import PayoutAccount, Wallet, WithdrawalRequest, _generate_reference

WITHDRAWAL_OTP_SUBJECT = "Confirm your withdrawal"


def get_or_create_wallet(*, user: User) -> Wallet:
    """Lazily provision a zero-balance wallet for `user` on first access.

    No post_save signal on User is used - wallets are created on demand so no
    wallet row exists until a user actually needs one (first credit or first
    wallet-detail view).
    """

    wallet, _created = Wallet.objects.get_or_create(user=user)
    return wallet


def get_wallet_totals(*, wallet: Wallet) -> dict:
    """Aggregate the dashboard's "Total amount earned" and "Pending payments"
    figures from Transaction history, rather than storing them as separate
    denormalized fields on Wallet - they're cheap to compute and can never
    drift out of sync with the transaction log.
    """

    totals = wallet.transactions.aggregate(
        total_earned=Sum(
            "amount",
            filter=Q(type=TransactionType.CREDIT, status=TransactionStatus.COMPLETED),
        ),
        pending_balance=Sum(
            "amount",
            filter=Q(type=TransactionType.DEBIT, status=TransactionStatus.PENDING),
        ),
    )
    return {
        "total_earned": totals["total_earned"] or Decimal("0.00"),
        "pending_balance": totals["pending_balance"] or Decimal("0.00"),
    }


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
            reference=_generate_reference(),
        )
        wallet.balance = wallet.balance + amount
        wallet.save(update_fields=["balance", "updated_datetime"])

    return txn


def list_transactions(*, user: User) -> QuerySet[Transaction]:
    """Return the transaction history for `user`'s wallet, newest first."""

    return Transaction.objects.filter(wallet__user=user)


def list_payout_accounts(*, user: User) -> QuerySet[PayoutAccount]:
    """Return `user`'s payout accounts, newest first."""

    return PayoutAccount.objects.filter(user=user)


def create_payout_account(
    *,
    user: User,
    account_type: str,
    bank_name: str,
    account_number: str,
    account_name: str,
    is_default: bool = False,
) -> BankAccount:
    """Add a payout account for `user`.

    The first account a user adds automatically becomes their default
    (matching the design, where the first-added account shows as selected
    on Settings -> Payment); explicitly passing is_default=True on a later
    account demotes any previous default.
    """

    require_role(user, IsCourseCreatorRole.allowed_roles)
    has_existing = BankAccount.objects.filter(user=user).exists()
    is_default = is_default or not has_existing

    with transaction.atomic():
        if is_default:
            BankAccount.objects.filter(user=user, is_default=True).update(is_default=False)
        return BankAccount.objects.create(
            user=user,
            account_type=account_type,
            bank_name=bank_name,
            account_number=account_number,
            account_name=account_name,
            is_default=is_default,
        )


def delete_bank_account(*, user: User, bank_account_id) -> None:
    """Remove one of `user`'s bank accounts.

    Raises NotFound if it doesn't exist or belongs to someone else.
    """

    require_role(user, IsCourseCreatorRole.allowed_roles)
    account = BankAccount.objects.filter(user=user, pk=bank_account_id).first()
    if account is None:
        raise exceptions.NotFound("Bank account not found.")
    account.delete()


def request_withdrawal(
    *, user: User, amount: Decimal, payout_account_id
) -> WithdrawalRequest:
    """Validate and create a PENDING_CONFIRMATION WithdrawalRequest, emailing
    an OTP the user must submit via confirm_withdrawal.

    Does not touch the wallet balance yet - funds are only reserved once the
    OTP is confirmed, so an abandoned/expired request never leaves stale
    reserved balance behind. Raises ValidationError if KYC is incomplete, the
    amount is below the minimum threshold, or exceeds the current balance.
    """

    require_role(user, IsCourseCreatorRole.allowed_roles)
    kyc_service.require_verified(user=user)

    minimum_withdrawal_threshold = (
        platform_settings_service.get_settings().minimum_withdrawal_threshold
    )
    if amount < minimum_withdrawal_threshold:
        raise exceptions.ValidationError(
            f"Minimum withdrawal amount is {minimum_withdrawal_threshold}."
        )

    wallet = get_or_create_wallet(user=user)
    if amount > wallet.balance:
        raise exceptions.ValidationError("Withdrawal amount exceeds available balance.")

    payout_account = BankAccount.objects.filter(user=user, pk=payout_account_id).first()
    if payout_account is None:
        raise exceptions.NotFound("Payout account not found.")

    withdrawal_request = WithdrawalRequest.objects.create(
        user=user,
        wallet=wallet,
        payout_account=payout_account,
        amount=amount,
    )

    _, raw_code = token_service.issue_numeric_code(
        user=user,
        purpose=TokenPurpose.WITHDRAWAL_CONFIRMATION,
        length=settings.WITHDRAWAL_OTP_LENGTH,
        expiry_minutes=settings.WITHDRAWAL_OTP_EXPIRY_MINUTES,
    )
    Notification.emit_email_notification(
        receivers=[user],
        subject=WITHDRAWAL_OTP_SUBJECT,
        template_name="emails/withdrawal_otp",
        context={
            "first_name": user.first_name,
            "code": raw_code,
            "amount": amount,
            "expiry_minutes": settings.WITHDRAWAL_OTP_EXPIRY_MINUTES,
        },
    )
    return withdrawal_request


def confirm_withdrawal(*, user: User, withdrawal_request_id, code: str) -> Transaction:
    """Verify the OTP for a pending WithdrawalRequest, then atomically
    reserve the funds and create the resulting PENDING debit Transaction.

    Re-validates the balance at confirmation time (not just at request time),
    since it may have changed in between. Raises NotFound if the request
    doesn't exist, isn't the caller's, or isn't awaiting confirmation.
    """

    require_role(user, IsCourseCreatorRole.allowed_roles)
    withdrawal_request = WithdrawalRequest.objects.filter(
        pk=withdrawal_request_id,
        user=user,
        status=WithdrawalRequestStatus.PENDING_CONFIRMATION,
    ).first()
    if withdrawal_request is None:
        raise exceptions.NotFound("Withdrawal request not found.")

    token_service.verify_token(
        user=user, purpose=TokenPurpose.WITHDRAWAL_CONFIRMATION, token=code
    )

    with transaction.atomic():
        wallet = Wallet.objects.select_for_update().get(pk=withdrawal_request.wallet_id)
        if withdrawal_request.amount > wallet.balance:
            raise exceptions.ValidationError(
                "Withdrawal amount exceeds available balance."
            )

        payout_account = withdrawal_request.payout_account
        txn = Transaction.objects.create(
            wallet=wallet,
            payout_account=payout_account,
            amount=withdrawal_request.amount,
            type=TransactionType.DEBIT,
            status=TransactionStatus.PENDING,
            description="Withdrawal request",
            recipient_account_name=payout_account.account_name,
            recipient_account_number=payout_account.account_number,
            recipient_provider_name=payout_account.bank_name,
        )
        wallet.balance = wallet.balance - withdrawal_request.amount
        wallet.save(update_fields=["balance", "updated_datetime"])

        withdrawal_request.status = WithdrawalRequestStatus.CONFIRMED
        withdrawal_request.transaction = txn
        withdrawal_request.confirmed_at = timezone.now()
        withdrawal_request.save(
            update_fields=["status", "transaction", "confirmed_at", "updated_datetime"]
        )

    return txn

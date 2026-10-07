import logging
from decimal import Decimal

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Q, QuerySet, Sum
from django.utils import timezone
from rest_framework import exceptions

from api.authentication.enums import TokenPurpose
from api.authentication.services import token_service
from api.authentication.services.activity_service import log_activity
from api.authorization import codenames
from api.authorization.services import permission_service
from api.courses.models import Course

# from api.platform.services import platform_settings_service
from api.payments.models.bankaccount_models import BankAccount
from api.payments.models.ledgeraccount_models import InternalAccount
from api.payments.models.transaction_model import Transaction, generate_reference
from api.payments.services.transaction_services import (
    internal_transfer,
)
from api.platform.services import platform_settings_service
from api.users.enums import UserActivityActionEnums, UserActivityCategoryEnums
from api.users.models import User
from api.users.services.kyc_services import kyc_submission_service
from api.wallet.enums import (
    TransactionStatus,
    TransactionType,
    WithdrawalRequestStatus,
)
from api.wallet.models import (
    Wallet,
    WithdrawalRequest,
    _generate_reference,
)
from api.wallet.tasks import dispatch_transfer_task
from core.models import TransferOutboxEvent
from shared.redis.redis_service import RedisService
from shared.services.email_service import EmailService

logger = logging.getLogger(__name__)

WITHDRAWAL_OTP_SUBJECT = "Confirm your withdrawal"

# Throttle withdrawal *requests* (not just confirms) to prevent OTP
# email-bombing: one active request per user per cooldown window.
_WITHDRAWAL_REQUEST_LOCK_TTL = 30


def get_or_create_wallet(*, user: User) -> Wallet:
    """Lazily provision a zero-balance wallet for `user` on first access.

    No post_save signal on User is used - wallets are created on demand so no
    wallet row exists until a user actually needs one (first credit or first
    wallet-detail view).
    """

    wallet, _ = Wallet.objects.get_or_create(user=user)
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


def credit_wallet(*, user: User, amount: Decimal, course: Course | None = None, description: str = "") -> Transaction:
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
            wallet_id=wallet.id,
            wallet_type=ContentType.objects.get_for_model(wallet),
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


def list_all_wallets(*, actor: User) -> QuerySet[Wallet]:
    """Return every creator wallet, for the admin finance view.

    Creator-facing wallet endpoints are gated on `earnings.manage_own`, which
    staff roles do not hold, so an Admin has no way to answer "what is this
    creator's balance?" there - these admin readers exist to close that.
    """

    permission_service.require_permission(actor, codenames.CREATORS_VIEW_WALLET)
    return Wallet.objects.select_related("user").order_by("-updated_datetime")


def list_all_transactions(*, actor: User) -> QuerySet[Transaction]:
    """Return every wallet transaction across all creators, newest first.

    select_related covers wallet__user and course because the admin serializer
    renders both per row; without it the list is one extra query per
    transaction.
    """

    permission_service.require_permission(actor, codenames.CREATORS_VIEW_WALLET)
    return Transaction.objects.select_related("course")  # wallet field is now a GenericForeign key


def list_all_withdrawal_requests(*, actor: User) -> QuerySet[WithdrawalRequest]:
    """Return every withdrawal request across all creators, newest first.

    This is the closest thing to a payout worklist the platform has. Note it
    is read-only: confirming a withdrawal debits the wallet and leaves a
    PENDING transaction, and nothing - here or anywhere - moves that to
    COMPLETED or FAILED yet, so an admin can see the queue but not settle it.
    """

    permission_service.require_permission(actor, codenames.CREATORS_VIEW_WALLET)
    return WithdrawalRequest.objects.select_related("user", "payout_account", "transaction")


def request_withdrawal(*, user: User, amount: Decimal, payout_account_id) -> WithdrawalRequest:
    """Validate and create a PENDING_CONFIRMATION WithdrawalRequest, emailing
    an OTP the user must submit via confirm_withdrawal.

    Does not touch the wallet balance yet - funds are only reserved once the
    OTP is confirmed, so an abandoned/expired request never leaves stale
    reserved balance behind. Raises ValidationError if KYC is incomplete, the
    amount is below the minimum threshold, or exceeds the current balance.
    """

    permission_service.require_permission(user, codenames.EARNINGS_MANAGE_OWN)
    platform_settings = platform_settings_service.get_settings()
    require_kyc = platform_settings.withdrawal_require_verification
    kyc_submission_service.require_verified(user=user, required=require_kyc)

    minimum_withdrawal_threshold = platform_settings_service.get_settings().minimum_withdrawal_threshold
    if amount < minimum_withdrawal_threshold:
        raise exceptions.ValidationError(f"Minimum withdrawal amount is {minimum_withdrawal_threshold}.")

    wallet = get_or_create_wallet(user=user)
    if amount > wallet.balance:
        raise exceptions.ValidationError("Withdrawal amount exceeds available balance.")

    payout_account = BankAccount.objects.filter(user=user, pk=payout_account_id).first()
    if payout_account is None:
        raise exceptions.NotFound("Payout account not found.")

    # Throttle: one withdrawal request (and thus one OTP email) per user per
    # cooldown window, to prevent OTP email-bombing.
    lock = RedisService.acquire_lock(f"withdrawal_request:{user.id}", _WITHDRAWAL_REQUEST_LOCK_TTL)
    if not lock:
        raise exceptions.Throttled(
            detail="A withdrawal was requested recently. Please wait before requesting another.",
            wait=_WITHDRAWAL_REQUEST_LOCK_TTL,
        )

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
        reference_request=withdrawal_request,
    )

    EmailService.send_withdrawal_otp_email(
        user_email=user.email,
        first_name=user.first_name,
        code=raw_code,
        amount=amount,
    )
    return withdrawal_request


def confirm_withdrawal(*, user: User, withdrawal_request_id, code: str) -> Transaction:
    """Verify the OTP for a pending WithdrawalRequest, then atomically
    reserve the funds and create the resulting PENDING debit Transaction.

    Re-validates the balance at confirmation time (not just at request time),
    since it may have changed in between. Raises NotFound if the request
    doesn't exist, isn't the caller's, or isn't awaiting confirmation.

    No MFA step-up here: this moves the caller's own earnings to the caller's
    own verified bank account, behind an emailed OTP, and needs
    `earnings.manage_own` (Course Creator and Writer by default). Actions on
    other people's money are gated separately, with MFA.
    """

    permission_service.require_permission(user, codenames.EARNINGS_MANAGE_OWN)

    # Atomically claim the request: only one concurrent confirm can transition
    # PENDING_CONFIRMATION -> CONFIRMED, which prevents a double-confirm race
    # from producing two payouts for a single withdrawal request.
    with transaction.atomic():
        claimed = WithdrawalRequest.objects.filter(
            pk=withdrawal_request_id,
            user=user,
            status=WithdrawalRequestStatus.PENDING_CONFIRMATION,
        ).update(
            status=WithdrawalRequestStatus.CONFIRMED,
            updated_datetime=timezone.now(),
        )
        if not claimed:
            raise exceptions.NotFound("Withdrawal request not found.")

        token = token_service.verify_token(user=user, purpose=TokenPurpose.WITHDRAWAL_CONFIRMATION, token=code)
        if token.reference_request_id is not None and str(token.reference_request_id) != str(withdrawal_request_id):
            raise exceptions.ValidationError("This code was not issued for this withdrawal request.")

        withdrawal_request = WithdrawalRequest.objects.select_for_update().get(pk=withdrawal_request_id)

        try:
            wallet = Wallet.objects.get(pk=withdrawal_request.wallet_id)
            if withdrawal_request.amount > wallet.balance:
                raise exceptions.ValidationError("Withdrawal amount exceeds available balance.")

            payout_account = withdrawal_request.payout_account
            account_number = payout_account.account_number
            bank_name = payout_account.bank_name
            account_name = payout_account.account_name
            reference = generate_reference()
        except Exception as exc:
            logger.error(f"Error preparing withdrawal confirmation for user {user.email}: {exc}")
            raise

        # move the amount from the user's wallet into the suspense(transit) account before initiating the transfer to ensure funds are reserved and to prevent double spending in case of retries
        credit_wallet = InternalAccount.objects.select_for_update().get(code_name="suspense")
        debit_wallet = Wallet.objects.select_for_update().get(pk=wallet.pk)
        internal_transfer(
            amount=withdrawal_request.amount,
            from_ledger=debit_wallet,
            to_ledger=credit_wallet,
            reference=reference,
            description="Withdrawal Request",
            fee=None,
            payout_account_id=payout_account.id,
            course_id=None,
            recipient_account_name=account_name,
            recipient_account_number=account_number,
            recipient_provider_name=bank_name,
        )

        platform_settings = platform_settings_service.get_settings()
        processor = platform_settings.payment_processor
        outbox_entry = TransferOutboxEvent.objects.create(
            user=user,
            amount=withdrawal_request.amount,
            status="PENDING",
            reference=reference,
            wallet=debit_wallet,
            reason="Wallet Withdrawal",
            transfer_request=withdrawal_request,
            transfer_processor=processor,
            bank_details=payout_account,
        )

        try:
            transaction.on_commit(
                lambda: dispatch_transfer_task.delay(outbox_entry.id)  # type: ignore
            )
        except Exception as task_exc:
            logger.error(f"Error dispatching transfer task for withdrawal {withdrawal_request.id}: {task_exc}")
            raise

        txn = Transaction.objects.get(reference=reference, type=TransactionType.DEBIT)
        withdrawal_request.transaction = txn
        withdrawal_request.confirmed_at = timezone.now()
        withdrawal_request.save(
            update_fields=[
                "transaction",
                "confirmed_at",
                "updated_datetime",
            ]
        )

    try:
        log_activity(
            user=user,
            category=UserActivityCategoryEnums.WALLET,
            action=UserActivityActionEnums.WITHDRAWAL_CONFIRMED,
            summary=f"User {user.email} confirmed a withdrawal of {withdrawal_request.amount} Naira.",
            actor_user=user,
            details={
                "user_id": str(user.id),
                "amount": str(withdrawal_request.amount),
                "reference": reference,
                "payout_account_id": str(payout_account.id),
            },
        )

    except Exception as audit_exc:
        # Log the audit error but do not interrupt the main flow of withdrawal confirmation
        logger.error(f"Error logging audit event for withdrawal confirmation: {audit_exc}")

    return txn

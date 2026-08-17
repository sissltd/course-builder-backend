import logging
from decimal import Decimal

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Q, QuerySet, Sum
from django.utils import timezone
from api.users.services import kyc_service
from rest_framework import exceptions

from api.authentication.enums import TokenPurpose
from api.authentication.services import token_service
from api.authentication.services.activity_service import log_activity
from api.courses.models import Course
from api.notification.models import Notification
from api.payments.models.bankaccount_models import BankAccount
from api.payments.models.ledgeraccount_models import InternalAccount
from api.payments.models.transaction_model import Transaction, generate_reference
from api.payments.services.transaction_services import (
    internal_transfer,
)
from api.platform.enums import PaymentProcessors
from api.platform.services import platform_settings_service
from api.users.enums import UserActivityActionEnums, UserActivityCategoryEnums
from api.users.models import User
from api.users.permissions import (
    IsAdminOrSuperAdminRole,
    IsCourseCreatorRole,
    require_role,
)
from api.wallet.enums import (
    TransactionStatus,
    TransactionType,
    WithdrawalRequestStatus,
)
from api.wallet.models import (
    PayoutAccount,
    Wallet,
    WithdrawalRequest,
    _generate_reference,
)
from api.wallet.tasks import dispatch_transfer_task
from core.models import TransferOutboxEvent
from shared.services.flutterwave_service import FlutterwaveService
from shared.services.paystack_service import PaystackService
from shared.utils.encryption import encrypt_field

logger = logging.getLogger(__name__)

WITHDRAWAL_OTP_SUBJECT = "Confirm your withdrawal"

class RecipientCreationError(Exception):
    """Raised when there is an error creating a transfer recipient."""


def _update_account_recipient_code(account: BankAccount, recipient_code: str, provider: PaymentProcessors):
    """Update the recipient code for a bank account based on the payment processor.
    A failure is logged, but not allowed disrupt the follow of operation
    """
    try:
        if provider == PaymentProcessors.PAYSTACK:
            account.paystack_recipient_code = recipient_code
        elif provider == PaymentProcessors.FLUTTERWAVE:
            account.flutterwave_recipient_code = recipient_code
        else:
            logger.warning(f"Unsupported payment processor: {provider}")
        account.save(update_fields=["paystack_recipient_code", "flutterwave_recipient_code", "updated_datetime"])
    except Exception as e:
        # Log the error but do not interrupt the main flow of withdrawal confirmation
        logger.error(f"Error updating account recipient code for account {account.id}: {e}")


def _transfer_processor_and_recipient_code(account: BankAccount) -> tuple[str, str]:
    """Attempts to get the payment processor to user for a an outgoing transfer and the recipient code.
    The processor is obtained from the PlatformSettings singleton, updatable by the admin

    We first check the recipient payout/bank account record, based on the payment process.
    If the record has the desired recipient code, we return it; otherwise we attempt to
    generate the code and update the record accordingly.

    We do return a NoneType; if we fail to get the recipient code, we raise an exception.
    This makes it easier for the caller to interprete the result
    """
    payment_processor = platform_settings_service.get_settings().payment_processor
    if payment_processor == PaymentProcessors.PAYSTACK:
        recipient_code = account.paystack_recipient_code
        if not recipient_code:
            successful, resp = PaystackService.create_transfer_recipient(
                account_number=account.account_number,
                bank_code=account.bank_code,
                name=account.account_name,
            )
            if successful and resp.get("recipient_code"):
                recipient_code = resp["recipient_code"]
                _update_account_recipient_code(account, recipient_code, payment_processor)
            else:
                logger.error(f"Failed to create Paystack transfer recipient for account {account.id}: {resp}")
                raise RecipientCreationError(
                    f"Failed to create Paystack transfer recipient: {resp.get('message', 'Unknown error')}"
                )
    elif payment_processor == PaymentProcessors.FLUTTERWAVE:
        recipient_code = account.flutterwave_recipient_code
        if not recipient_code:
            recipient_code = FlutterwaveService().get_recipient_id(
                account_number=account.account_number,
                bank_code=account.bank_code,
                account_name=account.account_name,
            )
            if not recipient_code:
                logger.error(f"Failed to create Flutterwave transfer recipient for account {account.id}")
                raise RecipientCreationError("Failed to create Flutterwave transfer recipient.")
            _update_account_recipient_code(account, recipient_code, payment_processor)
    else:
        raise ValueError(f"Unsupported payment processor: {payment_processor}")
    return payment_processor, recipient_code


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


def list_payout_accounts(*, user: User) -> QuerySet[PayoutAccount]:
    """Return `user`'s payout accounts, newest first."""

    return PayoutAccount.objects.filter(user=user)


def list_all_wallets(*, actor: User) -> QuerySet[Wallet]:
    """Return every creator wallet, for the admin finance view.

    Creator-facing wallet endpoints are gated on IsCourseCreatorRole, so an
    Admin is 403'd from all of them and has no way to answer "what is this
    creator's balance?" - these admin readers exist to close that.
    """

    require_role(actor, IsAdminOrSuperAdminRole.allowed_roles)
    return Wallet.objects.select_related("user").order_by("-updated_datetime")


def list_all_transactions(*, actor: User) -> QuerySet[Transaction]:
    """Return every wallet transaction across all creators, newest first.

    select_related covers wallet__user and course because the admin serializer
    renders both per row; without it the list is one extra query per
    transaction.
    """

    require_role(actor, IsAdminOrSuperAdminRole.allowed_roles)
    return Transaction.objects.select_related(
        "course"
    )  # wallet field is now a GenericForeign key


def list_all_withdrawal_requests(*, actor: User) -> QuerySet[WithdrawalRequest]:
    """Return every withdrawal request across all creators, newest first.

    This is the closest thing to a payout worklist the platform has. Note it
    is read-only: confirming a withdrawal debits the wallet and leaves a
    PENDING transaction, and nothing - here or anywhere - moves that to
    COMPLETED or FAILED yet, so an admin can see the queue but not settle it.
    """

    require_role(actor, IsAdminOrSuperAdminRole.allowed_roles)
    return WithdrawalRequest.objects.select_related(
        "user", "payout_account", "transaction"
    )


def create_payout_account(
    *,
    user: User,
    account_type: str,
    bank_name: str,
    account_number: str,
    account_name: str,
    bank_code: str,
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
            BankAccount.objects.filter(user=user, is_default=True).update(
                is_default=False
            )
        return BankAccount.objects.create(
            user=user,
            account_type=account_type,
            bank_name=bank_name,
            account_number=encrypt_field(account_number),
            account_name=account_name,
            is_default=is_default,
            bank_code=bank_code,
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

    No MFA step-up here: require_role below only ever admits
    IsCourseCreatorRole.allowed_roles (COURSE_CREATOR/STAFF_WRITER) - an
    ADMIN/SUPER_ADMIN account can never reach this function at all, so an
    MFA-mandated-role check would be unreachable dead code. The financial
    actions an Admin/Super Admin can actually perform in this codebase are
    PlatformSettings threshold changes and category pricing, both gated by
    IsMFAVerifiedForSession at the view layer instead.
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

    try:
        wallet = Wallet.objects.get(pk=withdrawal_request.wallet_id)
        if withdrawal_request.amount > wallet.balance:
            raise exceptions.ValidationError(
                "Withdrawal amount exceeds available balance."
            )

        payout_account = withdrawal_request.payout_account
        recipient_code = payout_account.paystack_recipient_code
        account_number = payout_account.account_number
        bank_name = payout_account.bank_name
        account_name = payout_account.account_name
        reference = generate_reference()
    except Exception as exc:
        logger.error(
            f"Error preparing withdrawal confirmation for user {user.email}: {exc}"
        )
        raise

    try:
        processor, recipient_code = _transfer_processor_and_recipient_code(payout_account)
    except RecipientCreationError as e:
        logger.error(f"Error creating transfer recipient for user {user.email}: {e}")
        raise exceptions.ValidationError(str(e))
    try:
        with transaction.atomic():
            # move the amount from the user's wallet into the suspense(transit) account before initiating the transfer to ensure funds are reserved and to prevent double spending in case of retries
            credit_wallet = InternalAccount.objects.select_for_update().get(
                code_name="suspense"
            )
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

            outbox_entry = TransferOutboxEvent.objects.create(
                user=user,
                amount=withdrawal_request.amount,
                recipient_code=recipient_code,
                status="PENDING",
                reference=reference,
                wallet=debit_wallet,
                reason="Wallet Withdrawal",
                transfer_request=withdrawal_request,
                transfer_processor=processor,
            )

            try:
                transaction.on_commit(
                    lambda: dispatch_transfer_task.delay(outbox_entry.id, provider=processor)  # type: ignore
                )
            except Exception as task_exc:
                logger.error(
                    f"Error dispatching Paystack transfer task for withdrawal {withdrawal_request.id}: {task_exc}"
                )
                raise

            txn = Transaction.objects.get(
                reference=reference, type=TransactionType.DEBIT
            )
            withdrawal_request.status = WithdrawalRequestStatus.CONFIRMED
            withdrawal_request.transaction = txn
            withdrawal_request.confirmed_at = timezone.now()
            withdrawal_request.save(
                update_fields=[
                    "status",
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
            logger.error(
                f"Error logging audit event for withdrawal initiation: {audit_exc}"
            )

    except Exception as exc:
        logger.error(f"Error initiating withdrawal for user {user.email}: {exc}")
        raise

    return txn

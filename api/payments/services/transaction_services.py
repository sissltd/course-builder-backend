import logging
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import QuerySet

from api.payments.models.transaction_model import Transaction
from api.users.models import User
from api.wallet.models import Wallet
from shared.services.paystack_service import PaystackService

logger = logging.getLogger(__name__)


class TransferRecipientError(Exception):
    """Could not generate transfer recipient code"""


def list_transactions(*, user: User) -> QuerySet[Transaction]:
    """Return the transaction history for `user`'s wallet, newest first."""

    wallet_ct = ContentType.objects.get_for_model(Wallet)

    try:
        wallet_id = Wallet.objects.values_list("id", flat=True).get(user=user)

        queryset = Transaction.objects.filter(
            wallet_type=wallet_ct, wallet_id=wallet_id
        )
    except Wallet.DoesNotExist:
        # Handle users without a wallet
        queryset = Transaction.objects.none()

    return queryset


def get_paystack_recipient_code(account_number, bank_code, account_name):
    try:
        successful, recipient_data = PaystackService.create_transfer_recipient(
            account_number=account_number, bank_code=bank_code, name=account_name
        )
    except Exception as exc:
        msg = f"Error creating transfer recipient: {exc}"
        logger.warning(msg)
        raise TransferRecipientError(msg)

    if not successful:
        logger.warning(recipient_data.get("message"))
        raise TransferRecipientError(recipient_data.get("message"))

    recipient_code = recipient_data.get("recipient_code")

    if not recipient_code:
        message = "Unable to resolve transfer recipient code"
        logger.warning(message)
        raise TransferRecipientError(message)
    return recipient_code


def get_wallet_for_update(wallet_type_id: int, object_id: int):
    content_type = ContentType.objects.get(id=wallet_type_id)
    model_class = content_type.model_class()
    target_object = model_class.objects.select_for_update().get(id=object_id)
    return target_object


@transaction.atomic
def create_transaction(
    wallet_id,
    wallet_type_id,
    amount,
    type,
    reference,
    status,
    course_id=None,
    payout_account_id=None,
    fee=None,
    description=None,
    recipient_account_name=None,
    recipient_account_number=None,
    recipient_provider_name=None,
):
    """
    Creating a transaction object and updating wallet balances are done in a single atomic transaction to ensure consistency. The wallet balances are updated first, and if that operation fails (e.g., due to insufficient funds), the transaction creation will be rolled back, preventing any inconsistent state where a transaction record exists without the corresponding balance update.
    """
    wallet = get_wallet_for_update(wallet_type_id, wallet_id)
    prefix = "DBT" if type == Transaction.TransactionType.DEBIT else "CRD"
    signed_amount = -amount if prefix == "DBT" else amount

    wallet.balance += Decimal(signed_amount)
    wallet.save()

    txn = Transaction.objects.create(
        wallet_id=wallet_id,
        wallet_type_id=wallet_type_id,
        amount=amount,
        course_id=course_id,
        payout_account_id=payout_account_id,
        reference=reference,
        fee=fee or Decimal("0.00"),
        type=type,
        status=status,
        description=description,
        recipient_account_name=recipient_account_name,
        recipient_account_number=recipient_account_number,
        recipient_provider_name=recipient_provider_name,
    )
    return txn


@transaction.atomic
def internal_transfer(
    amount,
    from_ledger,
    to_ledger,
    reference,
    description,
    fee=None,
    payout_account_id=None,
    course_id=None,
    recipient_account_name=None,
    recipient_account_number=None,
    recipient_provider_name=None,
):
    """An internal transfer between two ledgers (wallets) within the system. This function creates a debit transaction for the `from_ledger` and a credit transaction for the `to_ledger`, using the same reference to link them together. The operation is atomic, ensuring that either both transactions are created successfully or neither is created in case of an error."""

    # using the same reference for both transactions to link them together
    try:
        # Debit from_ledger
        create_transaction(
            wallet_id=from_ledger.id,
            wallet_type_id=ContentType.objects.get_for_model(from_ledger).id,
            amount=amount,
            type=Transaction.TransactionType.DEBIT,
            reference=reference,
            status=Transaction.TransactionStatus.COMPLETED,
            course_id=course_id,
            payout_account_id=payout_account_id,
            fee=fee,
            description=description,
            recipient_account_name=recipient_account_name,
            recipient_account_number=recipient_account_number,
            recipient_provider_name=recipient_provider_name,
        )

        # Credit to_ledger
        create_transaction(
            wallet_id=to_ledger.id,
            wallet_type_id=ContentType.objects.get_for_model(to_ledger).id,
            amount=amount,
            type=Transaction.TransactionType.CREDIT,
            reference=reference,
            status=Transaction.TransactionStatus.COMPLETED,
            course_id=course_id,
            payout_account_id=payout_account_id,
            fee=fee,
            description=description,
            recipient_account_name=recipient_account_name,
            recipient_account_number=recipient_account_number,
            recipient_provider_name=recipient_provider_name,
        )
    except Exception as e:
        logger.error(f"Internal transfer failed: {e}")
        raise

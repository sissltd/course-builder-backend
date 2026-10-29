# tasks.py
import logging

import requests
from celery import shared_task
from django.db import transaction
from django.utils import timezone

from api.authentication.services.activity_service import log_activity
from api.notification.models import Notification
from api.payments.models.ledgeraccount_models import InternalAccount
from api.payments.models.transaction_model import Transaction
from api.payments.services import transaction_services
from api.users.enums import UserActivityActionEnums, UserActivityCategoryEnums
from api.wallet.enums import TransactionStatus, TransactionType, WithdrawalRequestStatus
from core.models import TransferOutboxEvent
from shared.utils.encryption import decrypt_field

from .models import Wallet, WithdrawalRequest

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
def dispatch_transfer_task(self, outbox_id):
    try:
        with transaction.atomic():
            # Lock outbox entry so multiple workers don't execute it concurrently
            entry = TransferOutboxEvent.objects.select_for_update().get(id=outbox_id)

            if entry.status == "SUBMITTED" or (
                entry.status == "PROCESSING" and self.request.retries == 0
            ):
                return f"Entry {outbox_id} already running or completed."

            entry.status = "PROCESSING"
            entry.save()

        try:
            from shared.services.payment_services.provider import get_payment_provider

            provider = get_payment_provider()
            bank_details = entry.bank_details

            successful, response_data = provider.initiate_transfer(
                amount_naira=entry.amount,
                account_number=decrypt_field(bank_details.account_number),
                bank_code=bank_details.bank_code,
                account_name=bank_details.account_name,
                reference=entry.reference,
                reason="Wallet Withdrawal",
                bankaccount_id=bank_details.id,
            )
        except Exception as exc:
            logger.error(f"Error initiating transfer for outbox {outbox_id}: {exc}")
            raise

        with transaction.atomic():
            entry = TransferOutboxEvent.objects.select_for_update().get(id=outbox_id)

            if successful:
                # Processor queued it successfully
                entry.status = "SUBMITTED"
                entry.transfer_code = response_data.get("id")
                entry.save()

                log_activity(
                    user=entry.user,
                    category=UserActivityCategoryEnums.WALLET,
                    action=UserActivityActionEnums.WITHDRAWAL_REQUESTED,
                    summary=f"User {entry.user.email} requested a withdrawal of {entry.amount} Naira.",
                    actor_user=entry.user,
                    details={
                        "amount": str(entry.amount),
                        "reference": entry.reference,
                    },
                )
                # TODO: Consider sending a notification to the user that their withdrawal request has been submitted successfully.
            else:
                # API rejected it decisively (e.g., bad recipient code) -> Reverse funds
                handle_transfer_failure(
                    entry, response_data.get("message", "API Error")
                )

    except requests.exceptions.RequestException as exc:
        # Network timeout or DNS failure: the transfer may or may not have
        # reached Flutterwave (ambiguous). Retry - the provider's
        # X-Idempotency-Key makes re-submission safe. Never reverse funds here.
        countdown = (2**self.request.retries) * 60
        logger.warning(
            f"Network error on outbox {outbox_id}. Retrying in {countdown}s..."
        )
        raise self.retry(exc=exc, countdown=countdown)

    except Exception as exc:
        # Fallback for unexpected bugs. Only reverse if the transfer was never
        # submitted to the processor; a PROCESSING entry is ambiguous (the API
        # call may have succeeded), so we leave funds in suspense for the
        # reconciliation sweep rather than risk a double payout + refund.
        logger.error(f"Unexpected error dispatching transfer {outbox_id}: {exc}")
        with transaction.atomic():
            entry = TransferOutboxEvent.objects.select_for_update().get(id=outbox_id)
            if entry.status == TransferOutboxEvent.Status.PENDING:
                handle_transfer_failure(entry, str(exc))
            else:
                entry.error_log = str(exc)
                entry.save()
        raise


def handle_transfer_failure(entry, error_message):
    """Reverses funds to the user's wallet when the processor decisively
    rejects the transfer before it is submitted. Safe to call once: the entry
    must be PENDING (never sent) - the caller is responsible for that.
    """
    entry.status = TransferOutboxEvent.Status.FAILED
    entry.error_log = error_message
    entry.save()

    credit_wallet = Wallet.objects.select_for_update().get(user=entry.user)
    debit_wallet = InternalAccount.objects.select_for_update().get(code_name="suspense")
    transaction_services.internal_transfer(
        amount=entry.amount,
        from_ledger=debit_wallet,
        to_ledger=credit_wallet,
        reference=entry.reference,
        description="Reversal of failed wallet withdrawal",
    )
    # Settle user-facing state so the balance no longer shows as pending.
    Transaction.objects.filter(reference=entry.reference, type=TransactionType.DEBIT).update(
        status=TransactionStatus.FAILED
    )
    if entry.transfer_request_id:
        WithdrawalRequest.objects.filter(pk=entry.transfer_request_id).update(status=WithdrawalRequestStatus.EXPIRED)

    log_activity(
        user=entry.user,
        category=UserActivityCategoryEnums.WALLET,
        action=UserActivityActionEnums.WITHDRAWAL_FAILED,
        summary=f"User {entry.user.email} had a withdrawal of {entry.amount} Naira fail.",
        actor_user=entry.user,
        details={
            "amount": str(entry.amount),
            "reference": entry.reference,
            "error_message": error_message,
        },
    )

    Notification.emit_email_notification(
        receivers=[credit_wallet.user.email],
        subject="Failed Withdrawal Notification",
        template_name="emails/failed_withdrawal",
        context={"first_name": credit_wallet.user.first_name, "amount": entry.amount},
    )


@shared_task
def reconcile_stuck_transfers_task():
    """Sweep for transfers submitted to the processor that never produced a
    terminal webhook, and surface them for reconciliation.

    A transfer can sit in SUBMITTED indefinitely if its webhook was lost. The
    funds remain in suspense and the user's transaction stays PENDING. This
    task does not guess the outcome - it logs the stuck entries so ops can
    query the processor's status and settle them, which is the only safe way
    to resolve an ambiguous payout.
    """
    threshold = timezone.now() - timezone.timedelta(hours=1)
    stuck = TransferOutboxEvent.objects.filter(
        status=TransferOutboxEvent.Status.SUBMITTED,
        updated_datetime__lt=threshold,
    )
    count = stuck.count()
    if count:
        logger.error(
            f"{count} transfer(s) stuck in SUBMITTED for over an hour - "
            "manual reconciliation against the processor is required: "
            f"{list(stuck.values_list('reference', flat=True))}"
        )
    return f"{count} stuck transfer(s) found."

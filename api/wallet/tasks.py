# tasks.py
import logging

import requests
from celery import shared_task
from django.db import transaction

from api.authentication.services.activity_service import log_activity
from api.notification.models import Notification
from api.payments.models.ledgeraccount_models import InternalAccount
from api.payments.services import transaction_services
from api.users.enums import UserActivityActionEnums, UserActivityCategoryEnums
from core.models import TransferOutboxEvent

from .models import Wallet

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
                account_number=bank_details.account_number,
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
        # Network timeout or DNS failure. We don't know if Paystack got it!
        countdown = (2**self.request.retries) * 60
        logger.warning(
            f"Network error on outbox {outbox_id}. Retrying in {countdown}s..."
        )
        raise self.retry(exc=exc, countdown=countdown)

    except Exception as exc:
        # Fallback security wrap for unexpected system bugs
        with transaction.atomic():
            entry = TransferOutboxEvent.objects.get(id=outbox_id)
            if entry.status == "PROCESSING":
                handle_transfer_failure(entry, str(exc))


def handle_transfer_failure(entry, error_message):
    """Reverses funds to user's wallet safely if Paystack explicitly rejects the payload"""
    entry.status = "FAILED"
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

import base64
import hashlib
import hmac
import logging
from decimal import Decimal

from django.db import transaction as django_transaction

from api.authentication.services.activity_service import log_activity
from api.notification.models import Notification
from api.payments.models.ledgeraccount_models import InternalAccount
from api.payments.models.transaction_model import Transaction
from api.payments.services import transaction_services
from api.platform.enums import PaymentProcessors
from api.users.enums import UserActivityActionEnums, UserActivityCategoryEnums
from api.wallet.enums import TransactionStatus, TransactionType, WithdrawalRequestStatus
from api.wallet.models import WithdrawalRequest
from core.models import TransferOutboxEvent

logger = logging.getLogger(__name__)

INTERNAL_TRANSIT_ACCOUNT_NAME = "suspense"
INTERNAL_TRANSIT_FLUTTERWAVE_ACCOUNT_NAME = "flutterwave_transfer"


class WebhookProcessingError(Exception):
    """Custom exception for errors during webhook processing."""


class NonRetryableWebhookError(WebhookProcessingError):
    """Webhook payload is invalid or unsupported, so retries will not help."""


class FlutterwaveWebhookServices:
    @staticmethod
    def verify_request_signature(payload, signature, secret_key):
        """Takes a request payload--typically unparsed raw request payload [request.data]--, a hash secret
        and a string signature. Generates a sha-256 hash of the payload, using secret_key, and compare with the provided signature

        Comparison is done with `hmac.compare`, instead of regular equality, to mitigate `timing attack`
        """
        computed_digest = hmac.new(secret_key.encode("utf-8"), payload, hashlib.sha256).digest()
        computed_hash = base64.b64encode(computed_digest).decode("utf-8")
        return hmac.compare_digest(computed_hash, signature)

    @staticmethod
    def parse_webhook_event(event_data):
        if not isinstance(event_data, dict):
            raise NonRetryableWebhookError("Webhook payload must be a JSON object")

        event_type = event_data.get("type")
        data = event_data.get("data", {})
        status = data.get("status", "").upper()
        if not event_type:
            raise NonRetryableWebhookError("Missing 'event' in webhook payload")

        match event_type:
            case "transfer.disburse":
                match status:
                    case "SUCCESSFUL":
                        return FlutterwaveWebhookServices._handle_transfer_success(data)
                    case _:
                        return FlutterwaveWebhookServices._handle_transfer_failure(data)
            case "transfer.failed" | "transfer.failure" | "transfer.reversed" | "transfer.reversal":
                return FlutterwaveWebhookServices._handle_transfer_failure(data)
            case _:
                raise NonRetryableWebhookError(f"Unhandled event type: {event_type}")

    @staticmethod
    def _resolve_entry(reference):
        """Fetch the outbox entry for a transfer webhook, raising a
        non-retryable error if it doesn't exist (nothing to reconcile)."""
        entry = TransferOutboxEvent.objects.filter(
            reference=reference, transfer_processor=PaymentProcessors.FLUTTERWAVE
        ).first()
        if not entry:
            logger.error(f"No TransferOutboxEvent found for reference {reference}")
            raise NonRetryableWebhookError(f"No TransferOutboxEvent found for reference {reference}")
        return entry

    @staticmethod
    def _webhook_amount(data):
        """Extract the kobo-denominated amount from the webhook payload and
        convert to naira, tolerating both flat and nested shapes."""
        raw = data.get("amount")
        if isinstance(raw, dict):
            raw = raw.get("value")
        try:
            return Decimal(str(raw)) / 100
        except Exception:
            return None

    @staticmethod
    @django_transaction.atomic
    def _handle_transfer_success(data):
        reference = data.get("reference")
        metadata = data.get("meta", {})
        logger.warning(
            f"Handling transfer success for reference {reference} with metadata {metadata}"
        )

        with django_transaction.atomic():
            # Lock and guard: only a SUBMITTED transfer may be settled. Any
            # other state (FAILED/already PROCESSED/PENDING) makes this a
            # duplicate or out-of-order delivery, so we no-op rather than
            # double-settle.
            entry = (
                TransferOutboxEvent.objects.select_for_update()
                .filter(
                    reference=reference,
                    transfer_processor=PaymentProcessors.FLUTTERWAVE,
                )
                .first()
            )
            if not entry:
                raise NonRetryableWebhookError(f"No TransferOutboxEvent found for reference {reference}")
            if entry.status != TransferOutboxEvent.Status.SUBMITTED:
                logger.warning(
                    f"Ignoring success webhook for {reference}: entry status is {entry.status}, not SUBMITTED."
                )
                return

            # The outbox amount is authoritative; validate the webhook amount
            # against it rather than trusting the payload to move money.
            webhook_amount = FlutterwaveWebhookServices._webhook_amount(data)
            amount = entry.amount
            if webhook_amount is not None and webhook_amount != amount:
                logger.error(
                    f"Amount mismatch for {reference}: webhook={webhook_amount}, outbox={amount}. Using outbox amount."
                )

            entry.status = TransferOutboxEvent.Status.PROCESSED
            entry.save()

            transaction_services.internal_transfer(
                amount=amount,
                from_ledger=InternalAccount.objects.select_for_update().get(code_name=INTERNAL_TRANSIT_ACCOUNT_NAME),
                to_ledger=InternalAccount.objects.select_for_update().get(
                    code_name=INTERNAL_TRANSIT_FLUTTERWAVE_ACCOUNT_NAME
                ),
                reference=reference,
                description=f"Transfer success for reference {reference}",
            )

            # Settle the user-facing transaction so the balance no longer
            # shows the withdrawal as pending.
            Transaction.objects.filter(reference=reference, type=TransactionType.DEBIT).update(
                status=TransactionStatus.COMPLETED
            )

            log_activity(
                user=entry.user,
                category=UserActivityCategoryEnums.WALLET,
                action=UserActivityActionEnums.WITHDRAWAL_COMPLETED,
                summary=f"User {entry.user.email} successfully withdrew {entry.amount} Naira.",
                actor_user=entry.user,
                details={"user_id": str(entry.user.id), "amount": str(entry.amount), "reference": reference},
            )

            Notification.emit_email_notification(
                receivers=[entry.user.email],
                subject="Successful Withdrawal Notification",
                template_name="emails/successful_withdrawal",
                context={"first_name": entry.user.first_name, "amount": entry.amount},
            )
            Notification.emit_in_app_notification(
                receivers=[entry.user],
                title="Successful Withdrawal Notification",
                content=f"You have successfully withdrawn {entry.amount} Naira.",
                metadata={"reference": reference},
                critical=True,
            )

    @staticmethod
    @django_transaction.atomic
    def _handle_transfer_failure(data):
        """The transfer failed (or was reversed) at the payment processor.

        [1] Mark the transfer outbox as failed
        [2] Reverse the internal transfer to the user's wallet: debit the
            transit account and credit the originating wallet

        Idempotent: only a SUBMITTED entry is reversed, so a duplicate
        failure webhook - or one arriving after a local failure already
        refunded the user - cannot refund twice.
        """
        reference = data.get("reference")
        metadata = data.get('meta', {})
        msg = metadata.get("reason", "Transfer failed")

        with django_transaction.atomic():
            entry = (
                TransferOutboxEvent.objects.select_for_update()
                .filter(
                    reference=reference,
                    transfer_processor=PaymentProcessors.FLUTTERWAVE,
                )
                .first()
            )
            if not entry:
                raise NonRetryableWebhookError(f"No TransferOutboxEvent found for reference {reference}")
            if entry.status != TransferOutboxEvent.Status.SUBMITTED:
                logger.warning(
                    f"Ignoring failure webhook for {reference}: entry status is {entry.status}, not SUBMITTED."
                )
                return

            # The outbox amount is authoritative; validate the webhook amount
            # against it rather than trusting the payload to move money.
            webhook_amount = FlutterwaveWebhookServices._webhook_amount(data)
            amount = entry.amount
            if webhook_amount is not None and webhook_amount != amount:
                logger.error(
                    f"Amount mismatch for {reference}: webhook={webhook_amount}, outbox={amount}. Using outbox amount."
                )

            entry.status = TransferOutboxEvent.Status.FAILED
            entry.error_log = msg
            entry.save()

            transaction_services.internal_transfer(
                amount=amount,
                from_ledger=InternalAccount.objects.select_for_update().get(code_name=INTERNAL_TRANSIT_ACCOUNT_NAME),
                to_ledger=entry.wallet,
                reference=reference,
                description=f"Reversal of failed transfer for reference {reference} by {entry.user.email}",
            )

            # Settle user-facing state: mark the debit transaction FAILED and
            # the withdrawal request EXPIRED so the funds are no longer
            # counted as pending.
            Transaction.objects.filter(reference=reference, type=TransactionType.DEBIT).update(
                status=TransactionStatus.FAILED
            )
            if entry.transfer_request_id:
                WithdrawalRequest.objects.filter(pk=entry.transfer_request_id).update(
                    status=WithdrawalRequestStatus.EXPIRED
                )

            try:
                log_activity(
                    user=entry.user,
                    category=UserActivityCategoryEnums.WALLET,
                    action=UserActivityActionEnums.WITHDRAWAL_FAILED,
                    summary=f"User {entry.user.email}'s withdrawal of {entry.amount} Naira failed.",
                    actor_user=entry.user,
                    details={
                        "user_id": str(entry.user.id),
                        "amount": str(entry.amount),
                        "reference": reference,
                        "reason": msg,
                    }
                )

            except Exception as e:
                logger.error(f"Failed to log audit event for failed transfer: {e!s}")

            try:
                Notification.emit_email_notification(
                    receivers=[entry.user.email],
                    subject="Failed Withdrawal Notification",
                    template_name="emails/failed_withdrawal",
                    context={
                        "first_name": entry.user.first_name,
                        "amount": entry.amount,
                    },
                )
                Notification.emit_in_app_notification(
                    receivers=[entry.user],
                    title="Failed Withdrawal Notification",
                    content=f"Your withdrawal of {entry.amount} Naira has failed.",
                    metadata={"reference": reference},
                    critical=True,
                )
            except Exception as e:
                logger.error(
                    f"Failed to send email notification for failed transfer: {e!s}"
                )

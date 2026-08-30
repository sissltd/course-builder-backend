import hashlib
import hmac
import logging

from django.db import transaction as django_transaction

from api.authentication.services.activity_service import log_activity
from api.notification.models import Notification
from api.payments.models.ledgeraccount_models import InternalAccount
from api.payments.services import transaction_services
from api.users.enums import UserActivityActionEnums, UserActivityCategoryEnums
from core.models import TransferOutboxEvent

logger = logging.getLogger(__name__)

INTERNAL_TRANSIT_ACCOUNT_NAME = "suspense"
INTERNAL_TRANSIT_PAYSTACK_ACCOUNT_NAME = "paystack_transfer"


class WebhookProcessingError(Exception):
    """Custom exception for errors during webhook processing."""


class NonRetryableWebhookError(WebhookProcessingError):
    """Webhook payload is invalid or unsupported, so retries will not help."""


class PaystackWebhookServices:
    @staticmethod
    def verify_paystack_webhook(payload, signature, secret_key):
        computed_hash = hmac.new(
            secret_key.encode("utf-8"), payload, hashlib.sha512
        ).hexdigest()

        return hmac.compare_digest(computed_hash, signature)

    @staticmethod
    def parse_webhook_event(event_data):
        try:
            if not isinstance(event_data, dict):
                raise NonRetryableWebhookError("Webhook payload must be a JSON object")

            event_type = event_data.get("event")
            data = event_data.get("data", {})
            if not event_type:
                raise NonRetryableWebhookError("Missing 'event' in webhook payload")

            match event_type:
                case "transfer.success":
                    return PaystackWebhookServices._handle_transfer_success(data)
                case "transfer.failed" | "transfer.reversed":
                    return PaystackWebhookServices._handle_transfer_failure(data)
                case _:
                    raise NonRetryableWebhookError(
                        f"Unhandled event type: {event_type}"
                    )

        except Exception as e:
            logger.error(f"Error processing Paystack webhook event: {e!s}")
            raise WebhookProcessingError(
                f"Error processing Paystack webhook event: {e!s}"
            )

    @staticmethod
    @django_transaction.atomic
    def _handle_transfer_success(data):
        reference = data.get("reference")
        metadata = data.get("metadata", {})
        logger.warning(
            f"Handling transfer success for reference {reference} with metadata {metadata}"
        )
        entry = TransferOutboxEvent.objects.filter(reference=reference).first()
        if not entry:
            logger.error(f"No TransferOutboxEvent found for reference {reference}")
            raise WebhookProcessingError(
                f"No TransferOutboxEvent found for reference {reference}"
            )

        with django_transaction.atomic():
            entry.status = "PROCESSED"
            entry.save()

            transaction_services.internal_transfer(
                amount=data.get("amount") / 100,  # Convert from kobo to naira
                from_ledger=InternalAccount.objects.select_for_update().get(
                    code_name=INTERNAL_TRANSIT_ACCOUNT_NAME
                ),
                to_ledger=InternalAccount.objects.select_for_update().get(
                    code_name=INTERNAL_TRANSIT_PAYSTACK_ACCOUNT_NAME
                ),
                reference=reference,
                description=f"Transfer success for reference {reference}",
            )

            log_activity(
                user=entry.user,
                category=UserActivityCategoryEnums.WALLET,
                action=UserActivityActionEnums.WITHDRAWAL_COMPLETED,
                summary=f"User {entry.user.email} successfully withdrew {entry.amount} Naira.",
                actor_user=entry.user,
                details={
                    "user_id": str(entry.user.id),
                    "amount": str(entry.amount),
                    "reference": reference,
                },
            )

            Notification.emit_email_notification(
                receivers=[entry.user.email],
                subject="Successful Withdrawal Notification",
                template_name="emails/successful_withdrawal",
                context={"first_name": entry.user.first_name, "amount": entry.amount},
            )

    @staticmethod
    @django_transaction.atomic
    def _handle_transfer_failure(data):
        reference = data.get("reference")
        msg = data.get("reason", "Transfer failed")

        with django_transaction.atomic():
            entry = TransferOutboxEvent.objects.get(reference=reference)
            entry.status = "PROCESSED"
            entry.save()

            transaction_services.internal_transfer(
                amount=data.get("amount") / 100,  # Convert from kobo to naira
                from_ledger=InternalAccount.objects.select_for_update().get(
                    code_name=INTERNAL_TRANSIT_ACCOUNT_NAME
                ),
                to_ledger=entry.wallet,
                reference=reference,
                description=f"Reversal of failed transfer for reference {reference} by {entry.user.email}",
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
                    },
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
            except Exception as e:
                logger.error(
                    f"Failed to send email notification for failed transfer: {e!s}"
                )

import logging

from celery import shared_task
from django.db import transaction

from api.platform.enums import PaymentProcessors
from api.webhooks.services.flutterwave_webhook_services import FlutterwaveWebhookServices
from api.webhooks.services.paystack_webhook_services import (
    NonRetryableWebhookError,
    PaystackWebhookServices,
)
from core.models import WebhookEvent

logger = logging.getLogger(__name__)


def _get_webhook_service(provider: PaymentProcessors):
    """Determine which webhook service to use based on the value of PlatformSettings.payment_processor."""
    if provider == PaymentProcessors.PAYSTACK:
        return PaystackWebhookServices()
    elif provider == PaymentProcessors.FLUTTERWAVE:
        return FlutterwaveWebhookServices()
    else:
        raise ValueError(f"Unsupported transfer provider: {provider}")


@shared_task(
    bind=True,
    max_retries=5,
)
def process_webhook_task(self, event_row_id):
    try:
        with transaction.atomic():
            # select_for_update() locks the row so concurrent workers don't touch it
            event = WebhookEvent.objects.select_for_update().get(id=event_row_id)

            if event.status in ["PROCESSED", "PROCESSING"]:
                return f"Event {event.event_id} already handled or running."

            event.status = "PROCESSING"
            event.save()

        webhook_service = _get_webhook_service(event.provider)
        webhook_service.parse_webhook_event(event.payload)

        with transaction.atomic():
            event = WebhookEvent.objects.select_for_update().get(id=event_row_id)
            event.status = "PROCESSED"
            event.error_message = None
            event.save()

    except WebhookEvent.DoesNotExist:
        logger.error(f"WebhookEvent row {event_row_id} not found.")
        return f"Row {event_row_id} missing."

    except NonRetryableWebhookError as exc:
        with transaction.atomic():
            event = WebhookEvent.objects.select_for_update().get(id=event_row_id)
            event.status = "FAILED"
            event.error_message = f"Non-retryable webhook error: {exc!s}"
            event.save()

        logger.warning(f"Webhook event {event_row_id} failed with non-retryable error: {exc!s}")
        return f"Event {event_row_id} failed: {exc!s}"

    except Exception as exc:
        # Fallback to update database state before Celery initiates the retry
        with transaction.atomic():
            event = WebhookEvent.objects.get(id=event_row_id)
            event.status = "FAILED"
            event.error_message = f"Attempt {self.request.retries}: {exc!s}"
            event.save()

        countdown = (2**self.request.retries) * 60  # [60, 120, 240...]; exponential backoff in seconds

        logger.warning(
            f"Task failed. Retrying event {event_row_id} in {countdown}s. "
            f"Retry count: {self.request.retries}/{self.max_retries}"
        )

        # Explicitly trigger the retry with the calculated delay
        raise self.retry(exc=exc, countdown=countdown)

import json
import logging
from decimal import Decimal, InvalidOperation

from decouple import config
from django.db import transaction
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from api.platform.enums import PaymentProcessors
from api.webhooks.services.flutterwave_webhook_services import (
    FlutterwaveWebhookServices,
)
from api.webhooks.tasks import process_webhook_task
from core.models import WebhookEvent
from shared.constants.environ import DJANGO_ENV
from shared.response.success import custom_success_response

logger = logging.getLogger(__name__)


# Public endpoint: no security requirement, so Swagger's padlock does
# not attach a bearer token to it.
@extend_schema(auth=[{}])
@method_decorator(csrf_exempt, name="dispatch")
class FlutterwaveWebhookView(APIView):
    authentication_classes = []  # public: a stale token must not 401 this
    permission_classes = [AllowAny]

    FLUTTERWAVE_SECRET_HASH = config("FLUTTERWAVE_SECRET_HASH", default="")

    @extend_schema(exclude=True)
    def post(self, request, *args, **kwargs):
        payload = request.body

        # Fail closed: with no secret configured, every signature is
        # forgeable, so refuse to process rather than silently accept.
        if not self.FLUTTERWAVE_SECRET_HASH:
            logger.error("FLUTTERWAVE_SECRET_HASH is not configured; rejecting webhook.")
            return Response(
                {"error": "Webhook not configured"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        # 1. Verify the Flutterwave signature
        signature = request.headers.get("flutterwave-signature")

        if not signature:
            if DJANGO_ENV == "production":
                return custom_success_response(
                    data={},
                    message="Invalid signature.",
                    status=status.HTTP_200_OK,
                )
            return Response({"error": "Missing signature"}, status=status.HTTP_401_UNAUTHORIZED)

        is_valid = FlutterwaveWebhookServices.verify_request_signature(
            payload=payload,
            signature=signature,
            secret_key=self.FLUTTERWAVE_SECRET_HASH,
        )

        if not is_valid:
            # Return 200 so the processor doesn't retry a forgery, but log
            # loudly: a burst of these means the secret is wrong or we're
            # being probed.
            logger.warning("Flutterwave webhook signature verification failed.")
            return custom_success_response(
                message="Signature failed.",
                status=status.HTTP_200_OK,
            )

        # 2. Parse the payload
        try:
            payload_dict = json.loads(payload)
            event_type = payload_dict.get("type")
            # Dedupe on Flutterwave's unique event id, NOT the transfer
            # reference - several legitimate events (disburse, then a later
            # reversal) share one reference and must not be collapsed.
            event_id = str(payload_dict.get("webhook_id") or payload_dict.get("id"))
            if event_id in ("None", ""):
                raise KeyError("id")
        except (ValueError, KeyError):
            return Response(
                {"error": "Malformed payload"}, status=status.HTTP_400_BAD_REQUEST
            )

        # 3. Save to Outbox Table and Trigger Celery Atomically
        amount = payload_dict.get("data", {}).get("amount")
        if isinstance(amount, dict):
            amount = amount.get("value")
        if amount is not None:
            try:
                amount = Decimal(str(amount)) / 100  # Convert kobo to naira
            except (InvalidOperation, ValueError, TypeError):
                # A non-numeric amount raises InvalidOperation, not ValueError.
                amount = None
        try:
            with transaction.atomic():
                # Check if we already received this to prevent double-logging
                event, created = WebhookEvent.objects.get_or_create(
                    event_id=event_id,
                    defaults={
                        "event_type": event_type,
                        "payload": payload_dict,
                        "status": "PENDING",
                        "amount": amount,
                        "provider": PaymentProcessors.FLUTTERWAVE,
                    },
                )

                if created or event.status == "FAILED":
                    # Queue the task for a brand-new event, and re-queue a
                    # FAILED one on redelivery so a transient failure isn't
                    # silently stranded.
                    if not created:
                        event.status = "PENDING"
                        event.save(update_fields=["status", "updated_datetime"])
                    transaction.on_commit(
                        lambda: process_webhook_task.delay(event.id)  # type: ignore
                    )

        except Exception:
            logger.exception("Error persisting Flutterwave webhook event.")
            return Response(
                {"error": "Database error"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # 4. Instantly respond 200 OK to Flutterwave (under 2 seconds)
        return Response({"status": "accepted"}, status=status.HTTP_200_OK)

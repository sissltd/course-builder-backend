import json
from decimal import Decimal

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
from api.webhooks.services.flutterwave_webhook_services import FlutterwaveWebhookServices
from api.webhooks.tasks import process_webhook_task
from core.models import WebhookEvent
from shared.constants.environ import DJANGO_ENV
from shared.response.success import custom_success_response


@method_decorator(csrf_exempt, name="dispatch")
class FlutterwaveWebhookView(APIView):
    permission_classes = [AllowAny]

    FLUTTERWAVE_SECRET_HASH = config("FLUTTERWAVE_SECRET_HASH")

    @extend_schema(exclude=True)
    def post(self, request, *args, **kwargs):
        payload = request.body

        # 1. Verify the Paystack Signature
        signature = request.headers.get("flutterwave-signature")

        if not signature and DJANGO_ENV == "production":
            return custom_success_response(
                data={},
                message="Invalid signature.",
                status=status.HTTP_200_OK,
            )

        is_valid = FlutterwaveWebhookServices.verify_request_signature(
            payload=payload,
            signature=signature,
            secret_key=self.FLUTTERWAVE_SECRET_HASH,
        )

        if not is_valid:
            return custom_success_response(
                message="Signature failed.",
                status=status.HTTP_200_OK,
            )

        # 2. Parse the payload
        try:
            payload_dict = json.loads(payload)
            event_type = payload_dict.get("type")
            event_id = payload_dict.get("data", {}).get("reference") or str(payload_dict.get("id"))
        except (ValueError, KeyError):
            return Response(
                {"error": "Malformed payload"}, status=status.HTTP_400_BAD_REQUEST
            )

        # 3. Save to Outbox Table and Trigger Celery Atomically
        amount = payload_dict.get("data", {}).get("amount")
        if amount is not None:
            try:
                amount = Decimal(amount) / 100  # Convert kobo to naira
            except (ValueError, TypeError):
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

                if created:
                    # Queue the task only if it's a brand new event.
                    transaction.on_commit(
                        lambda: process_webhook_task.delay(event.id)  # type: ignore
                    )

        except Exception:
            # Log this error internally
            return Response(
                {"error": "Database error"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # 4. Instantly respond 200 OK to Paystack (under 2 seconds)
        return Response({"status": "accepted"}, status=status.HTTP_200_OK)

import json
from decimal import Decimal

from django.db import transaction
from drf_spectacular.utils import extend_schema_view
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from api.webhooks.services.paystack_webhook_services import WebhookServices
from api.webhooks.tasks import process_paystack_webhook_task
from core.models import PaystackWebhookEvent
from shared.constants.environ import DJANGO_ENV
from shared.constants.paystack import PAYSTACK_SECRET_KEY
from shared.response.success import custom_success_response


@extend_schema_view(exclude=True)
class PaystackWebhookView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        payload = request.body

        # 1. Verify the Paystack Signature
        signature = request.headers.get("x-paystack-signature")

        if not signature and DJANGO_ENV == "production":
            return custom_success_response(
                data={},
                message="Invalid signature.",
                status=status.HTTP_200_OK,
            )

        is_valid = WebhookServices.verify_paystack_webhook(
            payload=payload,
            signature=signature,
            secret_key=PAYSTACK_SECRET_KEY,
        )

        if not is_valid:
            return custom_success_response(
                message="Signature failed.",
                status=status.HTTP_200_OK,
            )

        # 2. Parse the payload
        try:
            data = json.loads(payload)
            event_type = data.get("event")
            event_id = data.get("data", {}).get("reference") or str(data.get("id"))
        except (ValueError, KeyError):
            return Response(
                {"error": "Malformed payload"}, status=status.HTTP_400_BAD_REQUEST
            )

        # 3. Save to Outbox Table and Trigger Celery Atomically
        amount = data.get("data", {}).get("amount")
        if amount is not None:
            try:
                amount = Decimal(amount) / 100  # Convert kobo to naira
            except (ValueError, TypeError):
                amount = None
        try:
            with transaction.atomic():
                # Check if we already received this to prevent double-logging
                event, created = PaystackWebhookEvent.objects.get_or_create(
                    event_id=event_id,
                    defaults={
                        "event_type": event_type,
                        "payload": data,
                        "status": "PENDING",
                        "amount": amount,
                    },
                )

                if created:
                    # Queue the task only if it's a brand new event.
                    transaction.on_commit(
                        lambda: process_paystack_webhook_task.delay(event.id)  # type: ignore
                    )

        except Exception:
            # Log this error internally
            return Response(
                {"error": "Database error"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # 4. Instantly respond 200 OK to Paystack (under 2 seconds)
        return Response({"status": "accepted"}, status=status.HTTP_200_OK)

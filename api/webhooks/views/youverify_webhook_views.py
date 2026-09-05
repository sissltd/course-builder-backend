import json
import logging

from decouple import config
from django.db import transaction
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from api.webhooks.services.youverify_webhook_services import YouverifyWebhookServices
from api.webhooks.tasks import process_youverify_webhook_task
from core.models import YouverifyWebhookOutboxEvent
from shared.constants.environ import DJANGO_ENV
from shared.response.success import custom_success_response

logger = logging.getLogger(__name__)


# Public endpoint: no security requirement, so Swagger's padlock does
# not attach a bearer token to it.
# @extend_schema(auth=[{}])
@method_decorator(csrf_exempt, name="dispatch")
class YouverifyWebhookView(APIView):
    authentication_classes = []  # public: a stale token must not 401 this
    permission_classes = [AllowAny]

    YOUVERIFY_WEBHOOK_SECRET = config("YOUVERIFY_WEBHOOK_SECRET", default="")

    @extend_schema(exclude=True)
    def post(self, request, *args, **kwargs):
        payload = request.body

        signature = request.headers.get("x-youverify-signature")

        if not signature and DJANGO_ENV == "production":
            return custom_success_response(
                data={},
                message="Invalid signature.",
                status=status.HTTP_200_OK,
            )

        is_valid = YouverifyWebhookServices.verify_request_signature(
            payload=payload,
            signature=signature,
            secret_key=self.YOUVERIFY_WEBHOOK_SECRET,
        )

        if not is_valid:
            return custom_success_response(
                message="Signature failed.",
                status=status.HTTP_200_OK,
            )

        # Parse the payload
        try:
            payload_dict = json.loads(payload)
            event_type = payload_dict.get("event")
            # YouVerify nests metadata under "data"; accept top-level as fallback.
            data = payload_dict.get("data") or {}
            metadata = data.get("metadata") or payload_dict.get("metadata") or {}
            kyc_request_id = metadata.get("kyc_request_id")
        except (ValueError, KeyError):
            return Response(
                {"error": "Malformed payload"}, status=status.HTTP_400_BAD_REQUEST
            )

        if not event_type or not kyc_request_id:
            # Cannot link this event to a KYC request. Acknowledge with 200 so
            # YouVerify stops retrying, and log for internal follow-up.
            logger.warning(
                "YouVerify webhook missing identifiers: event=%s kyc_request_id=%s",
                event_type,
                kyc_request_id,
            )
            return Response({"status": "ignored"}, status=status.HTTP_200_OK)

        # Save to Outbox Table and Trigger Celery Atomically
        try:
            with transaction.atomic():
                # Check if we already received this to prevent double-logging
                event, created = YouverifyWebhookOutboxEvent.objects.get_or_create(
                    kyc_request_id=kyc_request_id,
                    defaults={
                        "event_type": event_type,
                        "payload": payload_dict,
                        "status": "PENDING",
                    },
                )

                if created:
                    # Queue the task only if it's a brand new event.
                    transaction.on_commit(lambda event_id=event.id: process_youverify_webhook_task.delay(event_id))

        except Exception:
            logger.exception("Failed to persist YouVerify webhook outbox event")
            return Response(
                {"error": "Database error"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Instantly respond 200 OK to YouVerify (under 2 seconds)
        return Response({"status": "accepted"}, status=status.HTTP_200_OK)

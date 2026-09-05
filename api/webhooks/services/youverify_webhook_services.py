import hashlib
import hmac
import logging

from django.db import transaction as django_transaction

from api.users.models.kyc_verification import KYCVerification
from api.users.services.kyc_identity_service import (
    apply_kyc_document_photo,
    persist_kyc_identity,
    update_kyc_response,
)

logger = logging.getLogger(__name__)


class WebhookProcessingError(Exception):
    """Custom exception for errors during webhook processing."""


class NonRetryableWebhookError(WebhookProcessingError):
    """Webhook payload is invalid or unsupported, so retries will not help."""


class YouverifyWebhookServices:
    @staticmethod
    def verify_request_signature(payload, signature, secret_key):
        """Takes a request payload--typically unparsed raw request payload [request.body]--, a hash secret
        and a string signature. Generates a sha-256 hash of the payload, using secret_key, and compare with the provided signature

        Comparison is done with `hmac.compare`, instead of regular equality, to mitigate `timing attack`
        """
        
        computed_signature = hmac.new(
            key=secret_key.encode('utf-8'),
            msg=payload,
            digestmod=hashlib.sha256
        ).hexdigest()

        return hmac.compare_digest(computed_signature, signature)

    @classmethod
    def parse_webhook_event(cls, event_data):
        try:
            if not isinstance(event_data, dict):
                raise NonRetryableWebhookError("Webhook payload must be a JSON object")

            event_type = event_data.get("event")
            data = event_data.get("data", {})
            status = data.get("status", "").upper()
            if not event_type:
                raise NonRetryableWebhookError("Missing 'event' in webhook payload")

            match event_type:
                case "identity.completed":
                    match status:
                        case 'found':
                            return cls._handle_identity_verification_found(data)
                        case _:
                            return cls._handle_identity_verification_not_found(data)
                case _:
                    raise NonRetryableWebhookError(
                        f"Unhandled event type: {event_type}"
                    )

        except Exception as e:
            logger.error(f"Error processing YouVerify webhook event: {e!s}")
            raise WebhookProcessingError(
                f"Error processing YouVerify webhook event: {e!s}"
            )

    @classmethod
    @django_transaction.atomic
    def _handle_identity_verification_found(cls, data):
        """YouVerify returns a success, user data is found
        Update the KYCVerification object
        Update the User object
        Upload the returned document image and save the url
        """
        try:
            metadata = data.get('metadata', {})
            kyc_id = metadata.get('kyc_request_id')
            kyc_request = KYCVerification.objects.get(id=kyc_id)
            user = kyc_request.user
            image =  data.pop('image', None)
            raw = {
                "first_name": data.get("firstName") or data.get("first_name"),
                "last_name": data.get("lastName") or data.get("last_name"),
                "gender": data.get("gender"),
                "date_of_birth": data.get("dateOfBirth") or data.get("date_of_birth"),
                "image": image,
            }
            update_kyc_response(kyc_request, "found", request_summary=None, response_summary=data)
            persist_kyc_identity(user, raw)
            apply_kyc_document_photo(str(user.id), image)
        except Exception as e:
            logger.error(f"Error handling Youverify identity verification found: {e!s}")
            raise WebhookProcessingError(
                f"Error handling Youverify identity verification found: {e!s}"
            )

    @classmethod
    @django_transaction.atomic
    def _handle_identity_verification_not_found(cls, data):
        try:
            # Implement the handling logic for identity verification not found
            metadata = data.get('metadata', {})
            kyc_id = metadata.get('kyc_request_id')
            kyc_request = KYCVerification.objects.get(id=kyc_id)
            update_kyc_response(kyc_request, "not_found", request_summary=None, response_summary=data)
        except Exception as e:
            logger.error(f"Error handling Youverify identity verification not found: {e!s}")
            raise WebhookProcessingError(
                f"Error handling Youverify identity verification not found: {e!s}"
            )

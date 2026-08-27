"""Celery tasks for KYC image uploads.

The SISSL government photo (NIN/BVN) and the liveness selfie are uploaded to DigitalOcean Spaces, which is slow enough to threaten the synchronous request's gateway-timeout budget. Both are best-effort background work, so they run here off the request path. The upload/persist logic lives in the service; these are thin wrappers that Celery can discover and dispatch.
"""

import logging

from celery import shared_task
from django.utils import timezone

from api.sissl_verification.services.sissl_service import SISSLServices
from api.users.enums import KYCDocumentType
from api.users.services.kyc_identity_service import persist_sissl_identity
from core.models import SISSLOutboxEvent

logger = logging.getLogger(__name__)

@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=10,
    name="users.call_sissl_kyc_verification",
)
def call_sissl_kyc_verification(self, event_id):
    """
    Calls the SISSL KYC verification service.
    """
    
    outbox_event = SISSLOutboxEvent.objects.filter(
        id=event_id, is_deleted=False, processed=False
    ).first()
    if not outbox_event:
        logger.warning(
            f"[users.call_sissl_kyc_verification] Outbox event with ID {event_id} not found or already processed."
        )
        return

    try:
        payload = outbox_event.payload
        event_type = outbox_event.event_type
        kyc_request = outbox_event.kyc_request
        id_number = payload.get("id_number")
        
        match event_type:
            case KYCDocumentType.NATIONAL_ID.value:
                try:
                    data = SISSLServices.nin_lookup(kyc_request.user, id_number, kyc_request=kyc_request)
                    persist_sissl_identity(kyc_request.user, data)
                except Exception as exc:
                    logger.error(
                        f"[users.call_sissl_kyc_verification] NIN lookup failed for user {kyc_request.user.id}: {exc}"
                    )
                    raise self.retry(exc=exc)
            case KYCDocumentType.BVN.value:
                try:
                    data = SISSLServices.bvn_lookup(kyc_request.user, id_number)
                    persist_sissl_identity(kyc_request.user, data)
                except Exception as exc:
                    logger.error(
                        f"[users.call_sissl_kyc_verification] BVN lookup failed for user {kyc_request.user.id}: {exc}"
                    )
                    raise self.retry(exc=exc)
            case _:
                logger.warning(
                    f"[users.call_sissl_kyc_verification] Unsupported event type: {event_type}"
                )


        SISSLOutboxEvent.objects.filter(
            id=outbox_event.id,
            is_deleted=False,
            processed=False,
        ).update(processed=True, updated_datetime=timezone.now())

    except Exception as exc:
        logger.error(f"[users.call_sissl_kyc_verification] Failed: {exc}")
        raise self.retry(exc=exc)



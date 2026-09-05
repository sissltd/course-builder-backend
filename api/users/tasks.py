"""Celery tasks for KYC image uploads.

The SISSL government photo (NIN/BVN) and the liveness selfie are uploaded to DigitalOcean Spaces, which is slow enough to threaten the synchronous request's gateway-timeout budget. Both are best-effort background work, so they run here off the request path. The upload/persist logic lives in the service; these are thin wrappers that Celery can discover and dispatch.
"""

import logging

from celery import shared_task
from django.utils import timezone

from api.sissl_verification.services.sissl_service import SISSLServices
from api.users.enums import KYCDocumentType
from api.users.services.kyc_identity_service import YouVerifyService, persist_kyc_identity, update_kyc_response
from core.models import KYCOutboxEvent

logging.basicConfig(level=logging.INFO)
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

    outbox_event = KYCOutboxEvent.objects.filter(id=event_id, is_deleted=False, processed=False).first()
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
                    persist_kyc_identity(kyc_request.user, data)
                except Exception as exc:
                    logger.error(
                        f"[users.call_sissl_kyc_verification] NIN lookup failed for user {kyc_request.user.id}: {exc}"
                    )
                    raise self.retry(exc=exc)
            case KYCDocumentType.BVN.value:
                try:
                    data = SISSLServices.bvn_lookup(kyc_request.user, id_number)
                    persist_kyc_identity(kyc_request.user, data)
                except Exception as exc:
                    logger.error(
                        f"[users.call_sissl_kyc_verification] BVN lookup failed for user {kyc_request.user.id}: {exc}"
                    )
                    raise self.retry(exc=exc)
            case _:
                logger.warning(
                    f"[users.call_sissl_kyc_verification] Unsupported event type: {event_type}"
                )

        KYCOutboxEvent.objects.filter(
            id=outbox_event.id,
            is_deleted=False,
            processed=False,
        ).update(processed=True, updated_datetime=timezone.now())

    except Exception as exc:
        logger.error(f"[users.call_sissl_kyc_verification] Failed: {exc}")
        raise self.retry(exc=exc)


def _handle_youverify_failure_response(data, kyc_request):
    logger.error(f"[users.call_youverify_kyc_verification] YouVerify KYC verification failed: {data}")
    kyc_request.kyc_response_summary = data
    kyc_request.kyc_provider = "youverify"
    kyc_request.save()


def _handle_youverify_success_response(data, kyc_request):
    from celery.exceptions import CeleryError

    id_type_dict = {
        KYCDocumentType.NATIONAL_ID.value: "nin",
        KYCDocumentType.BVN.value: "bvn",
        KYCDocumentType.INTERNATIONAL_PASSPORT.value: "passport",
    }

    first_name = kyc_request.user.first_name
    last_name = kyc_request.user.last_name
    date_of_birth = kyc_request.date_of_birth
    id_number = kyc_request.id_number
    country_code = kyc_request.country_of_issue
    entity_id = data.get("entity_id")
    id_type = id_type_dict.get(kyc_request.document_type)

    kyc_request.kyc_provider = "youverify"
    kyc_request.kyc_entity_id = entity_id
    kyc_request.save()

    if not entity_id:
        logger.error(
            f"[users.call_youverify_kyc_verification] No entity_id returned from create_entity for user {kyc_request.user.id}; response: {data}"
        )
        return

    try:
        data = youverify_entity_verify_response = YouVerifyService.verify_entity(
            entity_id=entity_id,
            id_type=id_type,
            id_number=id_number,
            country_code=country_code,
            first_name=first_name,
            last_name=last_name,
            date_of_birth=date_of_birth,
            metadata_dict={"kyc_verification_id": str(kyc_request.id)},
        )

    except CeleryError:
        # Let retries, ignores, and rejects bubble up to Celery safely
        raise
    except Exception as exc:
        logger.error(
            f"[users.call_youverify_kyc_verification] {id_type} lookup failed for user {kyc_request.user.id}: {exc}"
        )
        # No webhook will ever arrive for a verify_entity call that never succeeded, so record the failure now.
        update_kyc_response(
            kyc_request, "failed", request_summary={"id_type": id_type}, response_summary={"error": str(exc)}
        )
    else:
        # Just log the successful verification response. Prefer the Webhook handling over direct response handling
        logger.info(
            f"[<><><><>users.call_youverify_kyc_verification] {id_type} lookup succeeded for user {kyc_request.user.id}: {youverify_entity_verify_response}"
        )


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=10,
    name="users.call_youverify_kyc_verification",
)
def call_youverify_kyc_verification(self, event_id):
    """
    Calls the YouVerify KYC verification service.

    - Call YouVerifyService.create_entity
    - save the entity ID to the KYCVerification record
    - Update the Outbox event status
    - Call YouVerifyService.verify_entity with the saved entity ID
    - Update the KYCVerification record with the verification result (Optionally wait for the webhook to handle that)
    """
    outbox_event = KYCOutboxEvent.objects.filter(id=event_id, is_deleted=False, processed=False).first()
    if not outbox_event:
        logger.warning(
            f"[users.call_youverify_kyc_verification] Outbox event with ID {event_id} not found or already processed."
        )
        return

    try:
        kyc_request = outbox_event.kyc_request

        first_name = kyc_request.user.first_name
        last_name = kyc_request.user.last_name
        email = kyc_request.user.email
        phone = kyc_request.user.phone_number
        gender = kyc_request.user.sex
        date_of_birth = kyc_request.date_of_birth
        nationality = kyc_request.user.country
        gender = gender.lower() if gender else None

        entity_creation_response = YouVerifyService.create_entity(
            {
                "entityType": "individual",
                "isSubjectConsent": True,
                "firstName": first_name,
                "lastName": last_name,
                "email": email,
                "phone": phone,
                "gender": gender,
                "dateOfBirth": str(date_of_birth),
                "nationality": nationality,
                "verifiedBy": "client"
            }
        )

        success = entity_creation_response.get("success", False)
        if not success:
            _handle_youverify_failure_response(entity_creation_response, kyc_request)
        else:
            _handle_youverify_success_response(data=entity_creation_response, kyc_request=kyc_request)

        KYCOutboxEvent.objects.filter(
            id=outbox_event.id,
            is_deleted=False,
            processed=False,
        ).update(processed=True, updated_datetime=timezone.now())

    except Exception as exc:
        logger.error(f"[users.call_youverify_kyc_verification] Failed: {exc}")
        raise self.retry(exc=exc)

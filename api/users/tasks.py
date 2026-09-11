"""Celery tasks for KYC management: image uploads, API calls, and other background KYC-related operations."""

import logging

from celery import shared_task
from django.utils import timezone

from api.users.enums import KYCDocumentType
from api.users.services.kyc_services.sissl_service import SISSLError, SISSLServices
from api.users.services.kyc_services.utils import persist_kyc_identity
from api.users.services.kyc_services.youverify_services import YouVerifyService
from core.models import KYCOutboxEvent
from shared.utils.encryption import decrypt_field

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=5,
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
        event_type = outbox_event.event_type
        kyc_request = outbox_event.kyc_request
        id_number = decrypt_field(kyc_request.id_number)
        
        match event_type:
            case KYCDocumentType.NATIONAL_ID.value:
                try:
                    data = SISSLServices.nin_lookup(kyc_request.user, id_number, kyc_request=kyc_request)
                    persist_kyc_identity(kyc_request.user, data)
                except SISSLError as exc:
                    logger.error(
                        f"[users.call_sissl_kyc_verification] NIN lookup failed for user {kyc_request.user.id}: {exc}"
                    )
                    raise self.retry(exc=exc)
                except Exception as exc:
                    logger.error(
                        f"[users.call_sissl_kyc_verification] NIN lookup failed for user {kyc_request.user.id}: {exc}"
                    )
                    raise self.retry(exc=exc)
            case KYCDocumentType.BVN.value:
                try:
                    data = SISSLServices.bvn_lookup(kyc_request.user, id_number)
                    persist_kyc_identity(kyc_request.user, data)
                except SISSLError as exc:
                    logger.error(
                        f"[users.call_sissl_kyc_verification] BVN lookup failed for user {kyc_request.user.id}: {exc}"
                    )
                    raise self.retry(exc=exc)
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
            processed=True,
        ).update(processed=True, updated_datetime=timezone.now())

    except Exception as exc:
        logger.error(f"[users.call_sissl_kyc_verification] Failed: {exc}")
        raise self.retry(exc=exc)



@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=5,
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
            YouVerifyService.handle_youverify_failed_entity_create(entity_creation_response, kyc_request)
        else:
            YouVerifyService.handle_youverify_successful_entity_create(
                data=entity_creation_response, kyc_request=kyc_request
            )

        KYCOutboxEvent.objects.filter(
            id=outbox_event.id,
            is_deleted=False,
            processed=True,
        ).update(processed=True, updated_datetime=timezone.now())

    except Exception as exc:
        logger.error(f"[users.call_youverify_kyc_verification] Failed: {exc}")
        raise self.retry(exc=exc)

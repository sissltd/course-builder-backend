
import logging

import requests
from decouple import config
from rest_framework.exceptions import ValidationError

from api.users.enums import KYCDocumentType
from api.users.services.kyc_services.utils import persist_kyc_identity, update_kyc_response
from shared.utils.encryption import decrypt_field

logger = logging.getLogger(__name__)


class YouVerifyService:
    """Service class for interacting with the YouVerify KYC provider.
    YouVerify Identitiy verification is a two-stage process involving entity creation and entity verification.
    """

    BASE_URL = config("YOUVERIFY_BASE_URL", default="")
    API_KEY = config("YOUVERIFY_API_KEY", default="")

    @classmethod
    def _get_headers(cls):
        secret_key = cls.API_KEY
        if not secret_key:
            logger.warning("YOUVERIFY_API_KEY is not set in environment variables.")
        return {
            "token": secret_key,
            "Content-Type": "application/json",
        }

    @classmethod
    def _parse_json_response(cls, response, context):
        """Parse a YouVerify response body, raising a descriptive error instead of a bare JSONDecodeError on empty/non-JSON bodies."""
        try:
            return response.json()
        except ValueError as exc:
            logger.error(
                "[YouVerifyService.%s] Non-JSON response (status %s): %s",
                context,
                response.status_code,
                response.text[:500],
            )
            raise ValueError(
                f"YouVerify {context} returned a non-JSON response (status {response.status_code})"
            ) from exc

    @classmethod
    def create_entity(cls, identity_data):
        """Create a YouVerify entity.

        Args:
            identity_data (dict): The identity information to be verified.
            {
                "entityType": "individual",
                "isSubjectConsent": true,
                "firstName": "John",
                "lastName": "Doe",
                "email": "john.doe@example.com",
                "phone": "+2348012345678",
                "gender": "male",
                "dateOfBirth": "1990-01-01",
                "nationality": "NG",
            }


        Returns:
            dict: The response from the YouVerify API, reformatted.
            {
                "success": True,
                "status_code": 201,
                "message": "Entity created successfully.",
                "entity_id": "ent_684f5cc5a47a3926763b83a7",
            }
        """
        url = f"{cls.BASE_URL}/v2/api/entities"
        headers = cls._get_headers()

        response = requests.post(url, json=identity_data, headers=headers)
        json_resp = cls._parse_json_response(response, context="create_entity")
        if isinstance(json_resp, dict):
            json_resp["entity_id"] = json_resp.pop("data", {}).get("id", None)
            if not json_resp["entity_id"]:
                logger.error("[YouVerifyService.create_entity] No entity id in response: %s", json_resp)
        return json_resp

    @classmethod
    def verify_entity(
        cls,
        entity_id,
        id_type,
        id_number,
        country_code="NG",
        first_name=None,
        last_name=None,
        date_of_birth=None,
        metadata_dict=None,
    ):
        """Verify the user's identity using YouVerify."""

        url = f"{cls.BASE_URL}/v2/api/entities/identity?entityId={entity_id}"
        headers = cls._get_headers()

        payload = {
            "entityType": "individual",
            "isSubjectConsent": True,
            "identity": {
                "id": id_number,
                "idType": id_type,
                "countryCode": country_code,
                "metadata": metadata_dict,
            },
        }

        # Conditional parameter mapping
        if id_type == "bvn":
            payload["identity"]["fullDetails"] = True
            payload["identity"]["premiumBVN"] = True

        elif id_type in ["nin", "vnin"]:
            payload["identity"]["premiumNin"] = True

        elif id_type == "passport":
            if not first_name or not last_name:
                raise ValidationError("First name and Last name are strictly required for Passport verification.")

            # Passports require explicit validation data to check against government files
            payload["identity"]["lastName"] = last_name
            payload["identity"]["validations"] = {
                "data": {
                    "firstName": first_name,
                    "dateOfBirth": date_of_birth,  # Format: YYYY-MM-DD
                }
            }
        else:
            logger.error(f"[kyc_identity_service.verify_entity] Unsupported ID type: {id_type}")
            raise ValueError(f"Unsupported ID type: {id_type}")

        response = requests.post(url, json=payload, headers=headers)
        json_resp = response.json()
        data = json_resp.get("data", {})
        if isinstance(data, dict):
            json_resp["entity_id"] = data.get("id", None)
        return json_resp

    @classmethod
    def handle_youverify_failed_entity_create(cls, data, kyc_request):
        logger.error(f"[users.call_youverify_kyc_verification] YouVerify KYC verification failed: {data}")
        kyc_request.kyc_response_summary = data
        kyc_request.kyc_provider = "youverify"
        kyc_request.save()

    @classmethod
    def handle_youverify_successful_entity_create(cls, data, kyc_request):
        from celery.exceptions import CeleryError

        id_type_dict = {
            KYCDocumentType.NATIONAL_ID.value: "nin",
            KYCDocumentType.BVN.value: "bvn",
            KYCDocumentType.INTERNATIONAL_PASSPORT.value: "passport",
        }

        first_name = kyc_request.user.first_name
        last_name = kyc_request.user.last_name
        date_of_birth = kyc_request.date_of_birth
        id_number = decrypt_field(kyc_request.id_number)
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
            payload = {
                "entity_id": entity_id,
                "id_type": id_type,
                "id_number": id_number,
                "country_code": country_code,
                "first_name": first_name,
                "last_name": last_name,
                "date_of_birth": date_of_birth,
                "metadata_dict": {"kyc_verification_id": str(kyc_request.id)},
            }
            resp_data = YouVerifyService.verify_entity(**payload)

        except CeleryError:
            # Let retries, ignores, and rejects bubble up to Celery safely
            raise
        except Exception as exc:
            logger.error(
                f"[users.call_youverify_kyc_verification] {id_type} lookup failed for user {kyc_request.user.id}: {exc}"
            )
            update_kyc_response(kyc_request, "failed", kyc_failure_message=str(exc))
        else:
            # Reverting to handling the verification response directly since the webhook may not be reliable
            request_summary = payload.copy()
            request_summary["date_of_birth"] = str(request_summary.pop("date_of_birth", None))
            if resp_data.get("success") is True:
                entity_data = resp_data.get("data", {})
                identity_data = entity_data.get("identityCheck", {})
                user = kyc_request.user
                raw = {
                    "first_name": identity_data.get("firstName") or identity_data.get("first_name"),
                    "last_name": identity_data.get("lastName") or identity_data.get("last_name"),
                    "gender": identity_data.get("gender"),
                    "date_of_birth": identity_data.get("dateOfBirth") or identity_data.get("date_of_birth"),
                    "image": identity_data.pop("image", None),
                }
                # Making the request_summary value json-serializable
                identity_data["dateOfBirth"] = str(identity_data.get("dateOfBirth"))
                _ = identity_data.pop("signature", None)
                print("entity_data:", identity_data.get("signature"), identity_data)
                update_kyc_response(kyc_request, "found")
                persist_kyc_identity(user, raw)
            else:
                logger.error(
                    f"[users.call_youverify_kyc_verification] Verification failed for user {kyc_request.user.id}: {resp_data}"
                )
                update_kyc_response(kyc_request, "failed", kyc_failure_message="Verification failed")

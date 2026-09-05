"""
KYC identity persistence.

Writes the SISSL-verified identity (name, DOB, gender, government photo) onto the
user's profile after a successful NIN/BVN lookup, marks the user as submitted
for review (entering the verification dashboard queue), and auto-saves a passed
liveness capture as the user's profile picture.

SISSL itself stays return-data-only (`api/sissl_verification/`), so this
write-side lives here and is called from the SISSL views. Best-effort by
contract — it never raises, because a persistence hiccup must not fail a
verification that already passed SISSL.
"""

import base64
import binascii
import logging

import requests
from decouple import config
from django.contrib.auth.models import User
from rest_framework.exceptions import ValidationError

from shared.services.storage_service import StorageError, StorageService

logger = logging.getLogger(__name__)

# Candidate keys for the date of birth across NIMC / NIBSS response shapes — the
# exact key is vendor-dependent, so we probe the common spellings.
_DOB_KEYS = ("dateOfBirth", "date_of_birth", "birthDate", "birthdate", "dob")
_DOC_FOLDER = "kyc-documents"


def _first_present(raw, *keys):
    for key in keys:
        value = raw.get(key)
        if value:
            return str(value)
    return ""


def _store_document_photo(image_value):
    """Decode a base64 government photo and store it in a PRIVATE bucket. Returns
    the object key, or "" on any failure (best-effort)."""
    if not image_value or not isinstance(image_value, str):
        return ""
    # Strip a data-URI prefix if present (e.g. "data:image/jpeg;base64,<...>").
    payload = image_value.split(",", 1)[-1] if image_value.startswith("data:") else image_value
    try:
        data = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        logger.warning("[<>KYCIdentity<>] KYC image was not valid base64; skipping photo")
        return ""
    try:
        return StorageService.upload_bytes(
            data, folder=_DOC_FOLDER, content_type="image/jpeg", acl="private"
        )
    except StorageError:
        logger.exception("[<>KYCIdentity<>] Failed to store KYC document photo")
        return ""


def apply_kyc_document_photo(user_id, image_value):
    """Upload the KYC government photo and record its key on the user's profile.

    Runs in a Celery worker off the request path — the Spaces upload is slow enough to threaten the synchronous NIN/BVN response's gateway-timeout budget.
    Non-raising and idempotent (a re-run just re-uploads and overwrites the key).
    """
    try:
        image_key = _store_document_photo(image_value)
        if not image_key:
            return

        user = User.objects.filter(id=user_id).first()
        if user is None:
            return
        user.kyc_document_image = image_key
        user.save(update_fields=["kyc_document_image", "updated_datetime"])
    except Exception:
        logger.exception("[<>KYCIdentity<>] apply_kyc_document_photo failed for user %s", user_id)


def persist_kyc_identity(user, raw):
    """Persist the KYC-verified identity onto ``user.profile`` and mark the user
    submitted for KYC review. Idempotent and non-raising."""
    try:
        if not isinstance(raw, dict):
            return

        update_fields = []

        first = _first_present(raw, "firstName", "first_name")
        if first:
            user.kyc_first_name = first
            update_fields.append("kyc_first_name")

        last = _first_present(raw, "lastName", "last_name")
        if last:
            user.kyc_last_name = last
            update_fields.append("kyc_last_name")

        gender = _first_present(raw, "gender")
        if gender:
            user.kyc_gender = gender
            update_fields.append("kyc_gender")

        dob = _first_present(raw, *_DOB_KEYS)
        if dob:
            user.kyc_date_of_birth = dob
            update_fields.append("kyc_date_of_birth")

        if update_fields:
            update_fields.append("updated_datetime")
            user.save(update_fields=update_fields)

        image_value = raw.get("image")
        if image_value:
            apply_kyc_document_photo(str(user.id), image_value)
    except Exception:
        logger.exception("[<>KYCIdentity<>] persist_sissl_identity failed")


def update_kyc_response(kyc_request, status, request_summary=None, response_summary=None):
    """
    Updates the KYC request with the SISSL/YOUVERIFY response data.
    """
    try:
        kyc_request.kyc_request_status = status
        kyc_request.kyc_request_summary = request_summary
        kyc_request.kyc_response_summary = response_summary
        kyc_request.save(
            update_fields=["kyc_request_status", "kyc_request_summary", "kyc_response_summary", "updated_datetime"]
        )
    except Exception:
        logger.exception("[<>KYCIdentity<>] update_kyc_response failed for KYC request %s", kyc_request.id)


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
        # json_resp = cls._parse_json_response(response, context="verify_entity")
        json_resp = response.json()
        data = json_resp.pop("data", {})
        if isinstance(data, dict):
            json_resp["entity_id"] = data.get("id", None)
        return json_resp

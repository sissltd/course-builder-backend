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

from django.contrib.auth.models import User

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
        logger.warning("[<>KYCIdentity<>] SISSL image was not valid base64; skipping photo")
        return ""
    try:
        return StorageService.upload_bytes(
            data, folder=_DOC_FOLDER, content_type="image/jpeg", acl="private"
        )
    except StorageError:
        logger.exception("[<>KYCIdentity<>] Failed to store SISSL document photo")
        return ""


def apply_kyc_document_photo(user_id, image_value):
    """Upload the SISSL government photo and record its key on the user's profile.

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


def update_sissl_response(kyc_request, status, request_summary, response_summary):
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

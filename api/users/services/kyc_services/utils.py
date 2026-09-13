"""
KYC identity persistence.

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

# Liveness selfies become the user's public profile picture, so they go in a
# public folder — unlike the government photos above, which stay private.
_AVATAR_FOLDER = "profile-pictures"

# Content types we accept from a data-URI prefix; anything else falls back to jpeg.
_DATA_URI_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}


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
        user.kyc_document_image = image_key # type: ignore
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
        
        address = raw.get("address")
        if address:
            user.kyc_address = address
            user.save(update_fields=["kyc_address", "updated_datetime"])

        phone = raw.get("mobile")
        if phone:
            user.kyc_phone = phone
            user.save(update_fields=["kyc_phone", "updated_datetime"])
    except Exception:
        logger.exception("[<>KYCIdentity<>] persist_sissl_identity failed")


def update_kyc_response(kyc_request, status, kyc_failure_message="", kyc_provider=None, kyc_entity_id=None):
    """
    Updates the KYC request with the SISSL/YOUVERIFY response data.
    """
    try:
        update_fields = ["kyc_request_status", "updated_datetime"]
        if kyc_failure_message:
            kyc_request.kyc_failure_message = kyc_failure_message
            update_fields.append("kyc_failure_message")
        if kyc_provider is not None:
            kyc_request.kyc_provider = kyc_provider
            update_fields.append("kyc_provider")
        if kyc_entity_id is not None:
            kyc_request.kyc_entity_id = kyc_entity_id
            update_fields.append("kyc_entity_id")
        kyc_request.kyc_request_status = status
        kyc_request.save(update_fields=update_fields)
    except Exception:
        logger.exception("[<>KYCIdentity<>] update_kyc_response failed for KYC request %s", kyc_request.id)


# Liveness handling utilities for KYC processes
def _upload_avatar_photo(image_value):
    """Decode a base64 selfie and store it in the PUBLIC profile-pictures folder.
    Returns the CDN URL, or "" on any failure (best-effort)."""
    if not image_value or not isinstance(image_value, str):
        return ""
    # Honour the data-URI content type when present (e.g. "data:image/png;base64,<...>");
    # bare base64 falls back to jpeg, same as the document-photo path.
    content_type = "image/jpeg"
    payload = image_value
    if image_value.startswith("data:"):
        header, _, payload = image_value.partition(",")
        declared = header.removeprefix("data:").split(";", 1)[0]
        if declared in _DATA_URI_CONTENT_TYPES:
            content_type = declared
    try:
        data = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        logger.warning("[<>KYCIdentity<>] liveness photo was not valid base64; skipping avatar")
        return ""
    try:
        file_key = StorageService.upload_bytes(
            data, folder=_AVATAR_FOLDER, content_type=content_type, acl="public-read"
        )
    except StorageError:
        logger.exception("[<>KYCIdentity<>] Failed to store liveness avatar")
        return ""
    return file_key


def save_liveness_avatar(user_id, image_value):
    """Upload a base64 liveness selfie and set it as the user's profile picture.

    Runs in a Celery worker off the request path — the same slow Spaces upload
    the document-photo task avoids in the request. Non-raising and idempotent.
    """
    try:
        url = _upload_avatar_photo(image_value)
        if not url:
            return
        from api.users.models import User

        user = User.objects.filter(id=user_id).first()
        if user is None:
            return
        user.liveness_selfie = url
        user.save(update_fields=["liveness_selfie", "updated_datetime"])
    except Exception:
        logger.exception("[<>KYCIdentity<>] save_liveness_avatar failed for user %s", user_id)


def persist_liveness_avatar(user, photo):
    """Save a PASSED liveness capture as in the user object. This is not automatically saved as the profile picture. The transition is subject to business decision.

    ``photo`` is whatever the client sent to the liveness endpoint:
      - a hosted URL  -> persisted as-is, synchronously (a fast DB write).
      - a base64 image -> the upload runs off the request path in a Celery
        worker (Spaces I/O is slow), then lands on the role-detail row.

    Mirrors onto whichever role-detail row the user has (homeowner / artisan /
    vendor / courier). Idempotent and non-raising — an avatar-save failure must
    never fail a verification that already passed SISSL.
    """
    try:
        if photo.startswith("http"):
            user.liveness_selfie = photo
            user.save(update_fields=["liveness_selfie", "updated_datetime"])
            return

        from api.users.tasks import store_liveness_avatar

        store_liveness_avatar.delay(str(user.id), photo)
    except Exception:
        logger.exception("[<>KYCIdentity<>] persist_liveness_avatar failed")

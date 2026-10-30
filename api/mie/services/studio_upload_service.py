"""Submission-scoped, retryable grants for existing platform object storage."""

import hashlib
import json
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework import exceptions

from api.mie.enums import SubmissionStatus
from api.mie.models import CourseSubmission, StudioMediaUpload
from api.mie.services.studio_review_service import ReviewConflict
from shared.services.storage_service import COURSE_UPLOAD_RULES, StorageService
from shared.utils.encryption import decrypt_field, encrypt_field


@transaction.atomic
def request_upload(*, developer, submission_id, asset_id, metadata):
    submission = (
        CourseSubmission.objects.select_for_update()
        .filter(pk=submission_id, developer=developer)
        .first()
    )
    if submission is None:
        raise exceptions.NotFound()
    course = submission.resulting_course
    if (
        submission.status != SubmissionStatus.APPROVED
        or course is None
        or course.video_provider != "DEVELOPER"
        or course.status not in ("AWAITING_VIDEO", "NEEDS_REVISION")
    ):
        raise ReviewConflict(
            "Platform video assignment is required before media upload."
        )
    purpose = metadata.get("purpose")
    if purpose not in ("COURSE_PREVIEW_VIDEO", "LESSON_VIDEO", "SUBTITLE"):
        raise exceptions.ValidationError("Unsupported Studio media purpose.")
    fingerprint = hashlib.sha256(
        json.dumps(metadata, sort_keys=True).encode()
    ).hexdigest()
    grant = StudioMediaUpload.objects.filter(
        submission=submission, asset_id=asset_id
    ).first()
    if grant:
        if fingerprint != grant.descriptor_hash:
            raise ReviewConflict("Asset ID was reused for different upload metadata.")
        if StorageService.file_exists(grant.file_key):
            if not grant.descriptor or not StorageService.verify_file(
                grant.file_key, **grant.descriptor
            ):
                raise ReviewConflict("Stored asset failed its integrity check.")
            return {
                "persisted": True,
                "media_url": StorageService.public_url(grant.file_key),
            }
        if grant.expires_at > timezone.now() + timedelta(seconds=60):
            return json.loads(decrypt_field(grant.upload_cipher))
    # No completed object is overwritten. An expired unused grant is replaced.
    params = {**metadata, "folder": COURSE_UPLOAD_RULES[purpose]["folder"]}
    params.pop("sha256")
    result = StorageService.request_upload(**params)
    result["media_url"] = StorageService.public_url(result["file_key"])
    StudioMediaUpload.objects.update_or_create(
        submission=submission,
        asset_id=asset_id,
        defaults={
            "descriptor_hash": fingerprint,
            "descriptor": {k: metadata[k] for k in ("sha256", "size", "content_type")},
            "purpose": purpose,
            "file_key": result["file_key"],
            "upload_cipher": encrypt_field(json.dumps(result)),
            "expires_at": timezone.now() + timedelta(seconds=result["expires_in"]),
        },
    )
    return result

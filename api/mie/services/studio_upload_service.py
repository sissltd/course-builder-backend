"""Submission-scoped upload grants for Soludesk Studio, and the check that a
video delivery's files are really in platform storage.

Studio asks for a grant per file, keyed by its own asset_id, PUTs the bytes
to the signed URL, then references `media_url` in a video delivery. The
grant is the same presign creators and developers already get
(StorageService.request_upload), so the same size, format, resolution and
codec rules apply, and the signed PUT carries only storage headers - the
MIE API key never goes to storage.

* Asking again for an asset that is not stored yet gives a fresh grant.
* An asset already stored with the same size and type answers "stored" with
  no URL; with a different size or type it is refused (AssetAlreadyStored).
* A delivery may reference platform storage only through a grant for its
  own idea, and only once the object is there at the declared size
  (MediaNotStored otherwise). Any other HTTPS URL is accepted and reported
  as external, as for every course push.
"""

from django.db import transaction
from django.utils import timezone
from rest_framework import exceptions

from api.mie.enums import SubmissionStatus
from api.mie.exceptions import AssetAlreadyStored, IdeaNotApproved, MediaNotStored
from api.mie.models import CourseSubmission, DeveloperAccount, StudioMediaUpload
from shared.services.storage_service import (
    COURSE_UPLOAD_RULES,
    FileTooLarge,
    InvalidFileType,
    InvalidUploadMetadata,
    StorageError,
    StorageService,
)

MAX_FILES_PER_GRANT = 50
"""Most files one grant request may cover - a full course's lesson videos."""

UPLOAD_REQUIRED = "upload_required"
STORED = "stored"

SIGNED_METADATA_FIELDS = ("size", "width", "height", "codec", "duration_seconds")


def grant_uploads(
    *, developer: DeveloperAccount, submission_id, files: list[dict]
) -> list[dict]:
    """A grant per file, in request order, for one of the caller's approved
    ideas.

    Raises NotFound for an idea that is not the caller's, IdeaNotApproved
    (409) for one that is not APPROVED, ValidationError naming the file for
    one that breaks an upload rule, and AssetAlreadyStored (409). On any of
    those no grant is recorded.
    """

    submission = CourseSubmission.objects.filter(
        developer=developer, id=submission_id
    ).first()
    if submission is None:
        raise exceptions.NotFound("Submission not found.")
    if submission.status != SubmissionStatus.APPROVED:
        raise IdeaNotApproved(
            "Uploads can only be granted for an approved idea. This idea is "
            f"{submission.status}."
        )

    existing = {
        upload.asset_id: upload
        for upload in StudioMediaUpload.objects.filter(
            submission=submission, asset_id__in=[item["asset_id"] for item in files]
        )
    }
    grants = []
    with transaction.atomic():
        for index, item in enumerate(files):
            upload = existing.get(item["asset_id"])
            if upload is not None and _is_stored(upload):
                if (upload.size, upload.content_type) != (
                    item["size"],
                    item["content_type"],
                ):
                    raise AssetAlreadyStored()
                grants.append(_stored_grant(upload))
                continue
            grants.append(
                _new_grant(
                    developer=developer,
                    submission=submission,
                    upload=upload,
                    item=item,
                    index=index,
                )
            )
    return grants


def verify_media(*, submission: CourseSubmission, content: dict) -> list[dict]:
    """Check every media URL of a video delivery's content and say where
    each one lives.

    Raises MediaNotStored for a platform-storage URL with no grant for this
    idea, or whose object is missing or not the declared size. Marks each
    verified upload's verified_at.
    """

    urls = _media_urls(content)
    uploads = {
        upload.media_url: upload
        for upload in StudioMediaUpload.objects.filter(
            submission=submission, media_url__in=urls
        )
    }
    storage_prefix = StorageService.public_url("")
    now = timezone.now()
    media = []
    for url in urls:
        upload = uploads.get(url)
        if upload is None:
            if url.startswith(storage_prefix):
                raise MediaNotStored(
                    f"{url} is in platform storage but was not granted to this "
                    "idea by POST /mie/v1/studio/uploads/."
                )
            media.append(
                {
                    "url": url,
                    "asset_id": None,
                    "stored": False,
                    "source": "external",
                    "size": None,
                }
            )
            continue
        if not _is_stored(upload):
            raise MediaNotStored(
                f"Asset {upload.asset_id} is not in storage at its declared size "
                f"of {upload.size} bytes. Finish its PUT, then deliver again."
            )
        upload.verified_at = now
        upload.save(update_fields=["verified_at", "updated_datetime"])
        media.append(
            {
                "url": url,
                "asset_id": upload.asset_id,
                "stored": True,
                "source": "platform",
                "size": upload.size,
            }
        )
    return media


def _is_stored(upload: StudioMediaUpload) -> bool:
    """True when the object is in storage at the size the grant declared."""

    return StorageService.object_size(upload.file_key) == upload.size


def _media_urls(content: dict) -> list[str]:
    """The preview video and each lesson's video_url, in order, once each.
    Embedded players are never ours to verify."""

    urls = [content.get("preview_video_url", "")]
    urls += [lesson.get("video_url", "") for lesson in content.get("lessons", [])]
    return list(dict.fromkeys(url for url in urls if url))


def _new_grant(*, developer, submission, upload, item: dict, index: int) -> dict:
    purpose = item["purpose"]
    try:
        result = StorageService.request_upload(
            filename=item["filename"],
            content_type=item["content_type"],
            folder=COURSE_UPLOAD_RULES[purpose]["folder"],
            purpose=purpose,
            **{
                field: item[field]
                for field in SIGNED_METADATA_FIELDS
                if item.get(field) is not None
            },
        )
    except (InvalidFileType, FileTooLarge, InvalidUploadMetadata) as exc:
        raise exceptions.ValidationError({"files": {index: [str(exc)]}}) from exc
    except StorageError as exc:
        raise exceptions.APIException(str(exc)) from exc

    fields = {
        "developer": developer,
        "file_key": result["file_key"],
        "media_url": StorageService.public_url(result["file_key"]),
        "purpose": purpose,
        "content_type": item["content_type"],
        "size": item["size"],
        "verified_at": None,
    }
    if upload is None:
        upload = StudioMediaUpload.objects.create(
            submission=submission, asset_id=item["asset_id"], **fields
        )
    else:
        for name, value in fields.items():
            setattr(upload, name, value)
        upload.save()
    return {
        "asset_id": upload.asset_id,
        "status": UPLOAD_REQUIRED,
        "method": "PUT",
        "upload_url": result["upload_url"],
        "upload_headers": result["upload_headers"],
        "expires_in": result["expires_in"],
        "media_url": upload.media_url,
        "size": upload.size,
    }


def _stored_grant(upload: StudioMediaUpload) -> dict:
    return {
        "asset_id": upload.asset_id,
        "status": STORED,
        "method": None,
        "upload_url": None,
        "upload_headers": None,
        "expires_in": None,
        "media_url": upload.media_url,
        "size": upload.size,
    }

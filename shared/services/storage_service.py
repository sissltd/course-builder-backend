import logging
import uuid
from urllib.parse import urlsplit

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from shared.constants.object_storage import (
    ACCESS_KEY_ID,
    ACCESS_SECRET_KEY,
    BUCKET_NAME,
    BUCKET_URL,
)

logger = logging.getLogger(__name__)


def _storage_endpoint():
    parsed = urlsplit(BUCKET_URL.rstrip("/"))
    bucket_prefix = f"{BUCKET_NAME}."
    if not parsed.scheme or not parsed.hostname:
        raise StorageError("BUCKET_URL must be an absolute URL.")
    if parsed.hostname.startswith(bucket_prefix):
        service_host = parsed.hostname[len(bucket_prefix) :]
        return f"{parsed.scheme}://{service_host}"
    return f"{parsed.scheme}://{parsed.hostname}"


def _public_base_url():
    return BUCKET_URL.rstrip("/")


def _storage_region():
    hostname_parts = (urlsplit(BUCKET_URL).hostname or "").split(".")
    if hostname_parts and hostname_parts[0] == BUCKET_NAME:
        hostname_parts.pop(0)
    return hostname_parts[0] if len(hostname_parts) > 2 else "us-east-1"


def _file_key_from_value(file_key):
    """Return the object key from either a raw key or path-style object URL."""
    if not file_key.startswith(("http://", "https://")):
        return file_key

    path = urlsplit(file_key).path.lstrip("/")
    bucket_prefix = f"{BUCKET_NAME}/"
    if path.startswith(bucket_prefix):
        path = path[len(bucket_prefix) :]
    if not path:
        raise FileNotFound("Invalid file URL format.")
    return path


# >>>>>>>>>>>>>>>>>>>> Allowed file types <<<<<<<<<<<<<<<<<<<<<<
ALLOWED_CONTENT_TYPES = {
    # Images
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    # Videos
    "video/mp4",
    "video/quicktime",
    "video/webm",
    # Documents
    "application/pdf",
    # Subtitles
    "application/x-subrip",
    "text/plain",
}

# Max file sizes per category (in bytes)
MAX_FILE_SIZES = {
    "image": 10 * 1024 * 1024,  # 10MB
    "video": 500 * 1024 * 1024,  # 500MB - course lesson and cover videos
    "application": 20 * 1024 * 1024,  # 20MB
    "text": 20 * 1024 * 1024,  # 20MB
}

MB = 1024 * 1024

COURSE_UPLOAD_RULES = {
    "COURSE_THUMBNAIL": {
        "folder": "thumbnails",
        "content_types": {"image/jpeg", "image/png"},
        "extensions": {"jpg", "jpeg", "png"},
        "max_size": 5 * MB,
        "dimensions": True,
        "aspect_ratio": (16, 9),
    },
    "LESSON_IMAGE": {
        "folder": "courses",
        "content_types": {"image/jpeg", "image/png", "image/webp", "image/gif"},
        "extensions": {"jpg", "jpeg", "png", "webp", "gif"},
        "max_size": 10 * MB,
    },
    "LESSON_VIDEO": {
        "folder": "courses",
        "content_types": {"video/mp4"},
        "extensions": {"mp4"},
        "max_size": 500 * MB,
        "dimensions": True,
        "codec": "h264",
    },
    "COURSE_PREVIEW_VIDEO": {
        "folder": "courses",
        "content_types": {"video/mp4"},
        "extensions": {"mp4"},
        "max_size": 100 * MB,
        "dimensions": True,
        "codec": "h264",
        "duration_range": (60, 120),
    },
    "SUBTITLE": {
        "folder": "courses",
        "content_types": {"application/x-subrip", "text/plain"},
        "extensions": {"srt"},
        "max_size": 20 * MB,
    },
}

# Presigned URL expiry (seconds)
PRESIGN_EXPIRY = 600  # 10 minutes


def max_size_for(content_type: str):
    """Byte cap for a MIME type's category, or None if uncapped."""

    return MAX_FILE_SIZES.get(content_type.split("/", 1)[0])


# >>>>>>>>>>>>>>>>>>>> Custom Exceptions <<<<<<<<<<<<<<<<<<<<<<


class InvalidFileType(Exception):
    pass


class FileTooLarge(Exception):
    pass


class InvalidUploadMetadata(Exception):
    pass


class StorageError(Exception):
    pass


class FileNotFound(Exception):
    pass


# >>>>>>>>>>>>>>>>>>>> S3 Client <<<<<<<<<<<<<<<<<<<<<<


def _get_s3_client():
    """
    Creates and returns a boto3 client configured for S3-compatible storage.

    The bucket service is S3-compatible, so the standard boto3 client can be
    used with its endpoint.
    """
    return boto3.client(
        "s3",
        region_name=_storage_region(),
        endpoint_url=_storage_endpoint(),
        aws_access_key_id=ACCESS_KEY_ID,
        aws_secret_access_key=ACCESS_SECRET_KEY,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
        ),
    )


# >>>>>>>>>>>>>>>>>>>> Storage Service <<<<<<<<<<<<<<<<<<<<<<


class StorageService:
    """
    Centralized file storage service using an S3-compatible bucket.

    The flow:
        1. Frontend calls request_upload() to get a presigned PUT URL
        2. Frontend uploads the file directly to object storage using that URL
        3. Frontend uses the returned CDN URL in subsequent API calls
        4. Backend never touches the file bytes — it's just a URL broker

    File organization in the bucket:
        uploads/{folder}/{uuid}.{extension}

        e.g., uploads/profiles/a1b2c3d4.jpg
              uploads/certificates/e5f6g7h8.pdf
              uploads/videos/i9j0k1l2.mp4
              uploads/jobs/m3n4o5p6.png
              uploads/chat/q7r8s9t0.jpg
    """

    @staticmethod
    def public_url(file_key):
        """Return the stable public URL for an object stored under file_key."""
        return f"{_public_base_url()}/{file_key.lstrip('/')}"

    @staticmethod
    def request_upload(
        filename,
        content_type,
        folder="general",
        size=None,
        purpose=None,
        width=None,
        height=None,
        duration_seconds=None,
        codec=None,
    ):
        """
        Generates a presigned PUT URL for direct upload to object storage.

        Args:
            filename:     Original filename (used to extract extension)
            content_type: MIME type (e.g., "image/jpeg")
            folder:       Storage folder (e.g., "profiles", "certificates", "videos", "jobs", "chat")
            size:         Optional byte count, checked against MAX_FILE_SIZES.
            purpose:      Creator course-media preset; required for course uploads.
            width:        Declared media width in pixels.
            height:       Declared media height in pixels.
            duration_seconds: Declared duration for course preview videos.
            codec:        Declared video codec; creator videos require h264.

        For creator course media, size is included in the signed PUT as
        Content-Length. Media dimensions, duration and codec are signed into
        object metadata so the registration/QA flow can audit what the client
        declared; inspecting the underlying codec remains a media-processing
        concern rather than a presign operation.

        Returns:
            dict with:
                upload_url:  Presigned PUT URL (frontend uploads here)
                upload_headers: Headers the frontend must send with the PUT
                file_url:    Temporary presigned GET URL for reading the file
                file_key:    The S3 key (used for deletion later)
                expires_in:  Seconds until the presigned URL expires
        """

        # [1] Validate content type
        if content_type not in ALLOWED_CONTENT_TYPES:
            raise InvalidFileType(
                f"File type '{content_type}' is not allowed. "
                f"Allowed types: {', '.join(sorted(ALLOWED_CONTENT_TYPES))}"
            )

        extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

        # Creator course uploads must declare their purpose so preview videos,
        # lesson videos, thumbnails and subtitles receive different rules.
        if folder in {"courses", "thumbnails"} and not purpose:
            raise InvalidUploadMetadata(
                "purpose is required for course and thumbnail uploads."
            )

        rule = COURSE_UPLOAD_RULES.get(purpose) if purpose else None
        if purpose and not rule:
            raise InvalidUploadMetadata(f"Unknown course upload purpose '{purpose}'.")
        if rule:
            if folder != rule["folder"]:
                raise InvalidUploadMetadata(
                    f"{purpose} uploads must use the '{rule['folder']}' folder."
                )
            if content_type not in rule["content_types"]:
                allowed = ", ".join(sorted(rule["content_types"]))
                raise InvalidFileType(f"{purpose} accepts only: {allowed}.")
            if extension not in rule["extensions"]:
                allowed = ", ".join(sorted(rule["extensions"]))
                raise InvalidFileType(
                    f"{purpose} requires one of these file extensions: {allowed}."
                )
            if size is None:
                raise InvalidUploadMetadata(f"size is required for {purpose} uploads.")
            if size > rule["max_size"]:
                raise FileTooLarge(
                    f"{purpose} files cannot exceed {rule['max_size'] // MB}MB."
                )
            if rule.get("dimensions"):
                if width is None or height is None:
                    raise InvalidUploadMetadata(
                        f"width and height are required for {purpose} uploads."
                    )
                if width < 1280 or height < 720:
                    raise InvalidUploadMetadata(
                        f"{purpose} requires a minimum resolution of 1280x720."
                    )
            if "aspect_ratio" in rule:
                ratio_width, ratio_height = rule["aspect_ratio"]
                if width * ratio_height != height * ratio_width:
                    raise InvalidUploadMetadata(
                        f"{purpose} must use a {ratio_width}:{ratio_height} aspect ratio."
                    )
            required_codec = rule.get("codec")
            if required_codec and (codec or "").lower() != required_codec:
                raise InvalidUploadMetadata(
                    f"{purpose} requires the {required_codec.upper()} codec."
                )
            duration_range = rule.get("duration_range")
            if duration_range and (
                duration_seconds is None
                or not duration_range[0] <= duration_seconds <= duration_range[1]
            ):
                raise InvalidUploadMetadata(
                    f"{purpose} must be between {duration_range[0]} and "
                    f"{duration_range[1]} seconds."
                )

        # Generic non-course uploads keep their existing category limit.
        if size is not None:
            limit = rule["max_size"] if rule else max_size_for(content_type)
            if limit is not None and size > limit:
                raise FileTooLarge(
                    f"File is {size} bytes; the limit for "
                    f"'{content_type}' is {limit} bytes "
                    f"({limit // (1024 * 1024)}MB)."
                )

        # [2] Generate unique filename (prevents overwrites and name collisions)
        unique_name = f"{uuid.uuid4().hex}.{extension or 'bin'}"
        file_key = f"uploads/{folder}/{unique_name}"

        logger.info(
            "[<>Storage<>] Upload requested: bucket=%s key=%s content_type=%s size=%s",
            BUCKET_NAME,
            file_key,
            content_type,
            size,
        )

        metadata = {}
        if purpose:
            metadata["upload-purpose"] = purpose
        if width is not None:
            metadata["width"] = str(width)
        if height is not None:
            metadata["height"] = str(height)
        if duration_seconds is not None:
            metadata["duration-seconds"] = str(duration_seconds)
        if codec:
            metadata["codec"] = codec.lower()

        put_params = {
            "Bucket": BUCKET_NAME,
            "Key": file_key,
            "ContentType": content_type,
        }
        if size is not None:
            put_params["ContentLength"] = size
        if metadata:
            put_params["Metadata"] = metadata

        # [3] Generate presigned PUT URL
        try:
            client = _get_s3_client()
            upload_url = client.generate_presigned_url(
                "put_object",
                Params=put_params,
                ExpiresIn=PRESIGN_EXPIRY,
            )

            # The bucket is private, so the direct bucket URL cannot be used
            # by the browser for playback. Generate a separate, short-lived
            # read URL for the just-created object; file_key remains the
            # durable value callers should persist for future URL refreshes.
            file_url = client.generate_presigned_url(
                "get_object",
                Params={"Bucket": BUCKET_NAME, "Key": file_key},
                ExpiresIn=PRESIGN_EXPIRY,
            )
        except ClientError as e:
            logger.error(f"[<>Storage<>] Presign failed: {e}")
            raise StorageError("Failed to generate upload URL. Please try again.")

        logger.info(
            "[<>Storage<>] Upload/read URLs generated: bucket=%s key=%s "
            "content_type=%s expires_in=%s",
            BUCKET_NAME,
            file_key,
            content_type,
            PRESIGN_EXPIRY,
        )

        upload_headers = {"Content-Type": content_type}
        upload_headers.update(
            {f"x-amz-meta-{key}": value for key, value in metadata.items()}
        )

        return {
            "upload_url": upload_url,
            "upload_headers": upload_headers,
            "file_url": file_url,
            "file_key": file_key,
            "expires_in": PRESIGN_EXPIRY,
        }

    @staticmethod
    def upload_bytes(
        data, *, folder="general", content_type="image/jpeg", acl="private"
    ):
        """Upload raw bytes server-side (e.g. a base64 image decoded from a vendor
        response). Unlike request_upload — which hands the frontend a presigned PUT —
        this stores the bytes directly from the backend.

        Use `acl="private"` for sensitive files (KYC documents) and serve them via
        generate_presigned_get(); the returned `file_key` is what to persist.
        """
        if content_type not in ALLOWED_CONTENT_TYPES:
            raise InvalidFileType(
                f"File type '{content_type}' is not allowed. "
                f"Allowed types: {', '.join(sorted(ALLOWED_CONTENT_TYPES))}"
            )

        extension = {
            "image/jpeg": "jpg",
            "image/png": "png",
            "image/webp": "webp",
            "image/gif": "gif",
            "application/pdf": "pdf",
        }.get(content_type, "bin")
        file_key = f"uploads/{folder}/{uuid.uuid4().hex}.{extension}"

        try:
            client = _get_s3_client()
            client.put_object(
                Bucket=BUCKET_NAME,
                Key=file_key,
                Body=data,
                ContentType=content_type,
                ACL=acl,
            )
        except ClientError as e:
            logger.error(f"[<>Storage<>] upload_bytes failed for {file_key}: {e}")
            raise StorageError("Failed to store the file. Please try again.")

        logger.info(
            f"[<>Storage<>] Bytes uploaded: {file_key} ({content_type}, acl={acl})"
        )
        return file_key

    @staticmethod
    def generate_presigned_get(file_key, expires_in=PRESIGN_EXPIRY):
        """Presigned GET URL for a (private) object, so the frontend can read it
        without the bucket being public. Accepts a raw key or a full CDN URL.
        Returns None on empty input or failure."""
        if not file_key:
            return None
        file_key = _file_key_from_value(file_key)

        logger.info(
            "[<>Storage<>] Presigned GET requested: bucket=%s key=%s expires_in=%s",
            BUCKET_NAME,
            file_key,
            expires_in,
        )

        try:
            client = _get_s3_client()
            download_url = client.generate_presigned_url(
                "get_object",
                Params={"Bucket": BUCKET_NAME, "Key": file_key},
                ExpiresIn=expires_in,
            )
            logger.info(
                "[<>Storage<>] Presigned GET generated: bucket=%s key=%s",
                BUCKET_NAME,
                file_key,
            )
            return download_url
        except ClientError as e:
            logger.error(f"[<>Storage<>] presigned GET failed for {file_key}: {e}")
            return None

    @staticmethod
    def delete_file(file_key):
        """
        Deletes a file from object storage by its key.

        Args:
            file_key: The S3 key (e.g., "uploads/profiles/a1b2c3d4.jpg")
                       Can also accept a full CDN URL — it will extract the key.

        Returns:
            bool: True if deleted, False if failed
        """

        file_key = _file_key_from_value(file_key)

        try:
            client = _get_s3_client()

            client.delete_object(
                Bucket=BUCKET_NAME,
                Key=file_key,
            )

            logger.info(f"[<>Storage<>] File deleted: {file_key}")
            return True

        except ClientError as e:
            logger.error(f"[<>Storage<>] Delete failed for {file_key}: {e}")
            return False

    @staticmethod
    def file_exists(file_key):
        """
        Checks if a file exists in object storage.

        Returns:
            bool: True if exists, False otherwise
        """

        try:
            client = _get_s3_client()
            client.head_object(
                Bucket=BUCKET_NAME,
                Key=file_key,
            )
            return True

        except ClientError:
            return False

import hashlib
import logging
import re
import uuid
from functools import lru_cache
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


def storage_endpoint():
    """S3-compatible API endpoint, derived from BUCKET_URL.

    Public so operational tooling (e.g. health probes) can build its own
    boto3 client against the same bucket configuration.
    """
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


def storage_region():
    """Bucket region, inferred from BUCKET_URL's hostname."""
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
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/csv",
    "application/csv",
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

MIN_MEDIA_WIDTH = 1280
MIN_MEDIA_HEIGHT = 720
"""Smallest accepted resolution for course media that declares dimensions."""

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
    "COURSE_DOCUMENT_IMPORT": {
        "folder": "course-imports",
        "content_types": {
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "text/plain",
            "text/csv",
            "application/csv",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/json",
        },
        "extensions": {"pdf", "docx", "txt", "csv", "xlsx", "json"},
        "mime_extensions": {
            "application/pdf": {"pdf"},
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {
                "docx"
            },
            "text/plain": {"txt"},
            "text/csv": {"csv"},
            "application/csv": {"csv"},
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {
                "xlsx"
            },
            "application/json": {"json"},
        },
        "max_size": 20 * MB,
    },
}

# Presigned URL expiry (seconds)
PRESIGN_EXPIRY = 600  # 10 minutes

PLAYBACK_URL_SECONDS = 4 * 60 * 60
"""How long a read URL served by the API stays valid. Long enough that a
lesson watched with pauses and seeks keeps working; reloading the course or
lesson serves a fresh one."""

_OBJECT_KEY = re.compile(r"[\w\-.!*'()/]+")
"""What a bare object key looks like, so a key stored in a media field can be
told apart from other text there (an embed snippet)."""


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
    Returns a boto3 client configured for S3-compatible storage.

    The bucket service is S3-compatible, so the standard boto3 client can be
    used with its endpoint. Building a client costs tens of milliseconds and
    a course response signs one URL per video, so clients are reused; boto3
    clients are thread-safe.
    """
    return _cached_s3_client(
        storage_region(), storage_endpoint(), ACCESS_KEY_ID, ACCESS_SECRET_KEY
    )


@lru_cache(maxsize=4)
def _cached_s3_client(region, endpoint, access_key_id, secret_access_key):
    return boto3.client(
        "s3",
        region_name=region,
        endpoint_url=endpoint,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
        ),
    )


def own_media_key(value):
    """The object key when `value` points at our bucket, else None.

    Accepts a bare key, the stable public_url(), or a presigned URL in either
    addressing style (the query string is ignored), so every form a client
    may have stored resolves to the same object.
    """
    if not value:
        return None
    if not value.startswith(("http://", "https://")):
        key = value.lstrip("/")
        return key if "/" in key and _OBJECT_KEY.fullmatch(key) else None

    url = value.split("?", 1)[0]
    public_base = StorageService.public_url("")
    if url.startswith(public_base):
        key = url[len(public_base) :]
    else:
        # A presigned URL names the bucket in its path on the shared
        # regional endpoint, which serves other buckets too.
        try:
            signed_base = f"{storage_endpoint()}/{BUCKET_NAME}/"
        except StorageError:
            return None
        if not url.startswith(signed_base):
            return None
        key = url[len(signed_base) :]
    return key or None


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
    def playable_url(value, expires_in=PLAYBACK_URL_SECONDS):
        """A URL a browser can open now.

        Our own objects - stored as a key, a public_url() or an old presigned
        URL - get a fresh signature, since the bucket is private; any other
        link (YouTube, a developer's CDN) passes through unchanged. Signing
        is local, so this is cheap enough to run per field on every read.
        Falls back to the stored value if signing fails.
        """
        key = own_media_key(value)
        if key is None:
            return value or ""
        try:
            signed = StorageService.generate_presigned_get(key, expires_in=expires_in)
        except StorageError as e:
            logger.error(f"[<>Storage<>] playable URL failed for {key}: {e}")
            signed = None
        return signed or value

    @staticmethod
    def durable_media_ref(value):
        """The form of a media reference that is safe to store.

        A presigned URL of our own expires, so it is stored as the object's
        public_url() instead and signed again on every read (playable_url).
        Bare keys and other links are already durable and are returned as is.
        """
        if not value or not value.startswith(("http://", "https://")):
            return value
        key = own_media_key(value)
        return StorageService.public_url(key) if key else value

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

        # [1] Validate content type. A purpose may allow extra types (e.g. the
        # XLSX/JSON import template) without opening them to every folder.
        purpose_types = COURSE_UPLOAD_RULES.get(purpose, {}).get("content_types", ())
        if content_type not in ALLOWED_CONTENT_TYPES and content_type not in purpose_types:
            raise InvalidFileType(
                f"File type '{content_type}' is not allowed. "
                f"Allowed types: {', '.join(sorted(ALLOWED_CONTENT_TYPES))}"
            )

        extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

        # Creator course uploads must declare their purpose so preview videos,
        # lesson videos, thumbnails and subtitles receive different rules.
        if folder in {"courses", "thumbnails", "course-imports"} and not purpose:
            raise InvalidUploadMetadata(
                "purpose is required for course, thumbnail, and course-import uploads."
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
            mime_extensions = rule.get("mime_extensions", {})
            if mime_extensions and extension not in mime_extensions.get(
                content_type, set()
            ):
                raise InvalidUploadMetadata(
                    "Filename extension does not match the uploaded document type."
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
                if width < MIN_MEDIA_WIDTH or height < MIN_MEDIA_HEIGHT:
                    raise InvalidUploadMetadata(
                        f"{purpose} requires a minimum resolution of "
                        f"{MIN_MEDIA_WIDTH}x{MIN_MEDIA_HEIGHT}."
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
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
            "text/plain": "txt",
            "text/csv": "csv",
            "application/csv": "csv",
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
    def upload_file(path, *, file_key, content_type, metadata=None):
        """Upload a local file under a key the caller chooses (the Production
        Engine names its files by content hash). Large files go up in parts.
        The object takes the bucket's default (private) access, like browser
        uploads; read it through generate_presigned_get(). `metadata` is
        stored as the object's x-amz-meta-* values."""

        extra_args = {"ContentType": content_type}
        if metadata:
            extra_args["Metadata"] = metadata
        try:
            _get_s3_client().upload_file(
                str(path), BUCKET_NAME, file_key, ExtraArgs=extra_args
            )
        except ClientError as e:
            logger.error(f"[<>Storage<>] upload_file failed for {file_key}: {e}")
            raise StorageError("Failed to store the file. Please try again.")
        return file_key

    @staticmethod
    def object_metadata(file_key):
        """The object's x-amz-meta-* values (what the uploader declared)."""

        try:
            response = _get_s3_client().head_object(Bucket=BUCKET_NAME, Key=file_key)
        except ClientError as e:
            logger.error(f"[<>Storage<>] head_object failed for {file_key}: {e}")
            raise StorageError("Failed to read the stored file. Please try again.")
        return response.get("Metadata", {})

    @staticmethod
    def download_file(file_key, path):
        """Download an object to a local file, for backend processing."""

        file_key = _file_key_from_value(file_key)
        try:
            _get_s3_client().download_file(BUCKET_NAME, file_key, str(path))
        except ClientError as e:
            logger.error(f"[<>Storage<>] download_file failed for {file_key}: {e}")
            raise StorageError("Failed to read the stored file. Please try again.")

    @staticmethod
    def download_bytes(file_key):
        """Download a private object's bytes for backend processing."""

        file_key = _file_key_from_value(file_key)
        try:
            response = _get_s3_client().get_object(Bucket=BUCKET_NAME, Key=file_key)
            return response["Body"].read()
        except ClientError as e:
            logger.error(f"[<>Storage<>] download_bytes failed for {file_key}: {e}")
            raise StorageError("Failed to read the uploaded file. Please try again.")

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
    def verify_file(file_key, *, sha256, size, content_type):
        """Hash bounded stored bytes before acknowledging a Studio asset."""
        try:
            result = _get_s3_client().get_object(Bucket=BUCKET_NAME, Key=file_key)
            body = result["Body"]
            try:
                if result["ContentLength"] != size or result["ContentType"] != content_type:
                    return False
                digest = hashlib.sha256()
                total = 0
                while chunk := body.read(min(1024 * 1024, size - total + 1)):
                    total += len(chunk)
                    if total > size:
                        return False
                    digest.update(chunk)
                return total == size and digest.hexdigest() == sha256
            finally:
                body.close()
        except ClientError as exc:
            if str(exc.response.get("Error", {}).get("Code")) in ("404", "NoSuchKey", "NotFound"):
                return False
            raise StorageError("Unable to verify platform media.") from exc

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

import logging
import uuid

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from shared.constants.digital_ocean import (
    DIGITAL_OCEAN_ACCESS_KEY,
    DIGITAL_OCEAN_BUCKET,
    DIGITAL_OCEAN_CDN_URL,
    DIGITAL_OCEAN_ENDPOINT,
    DIGITAL_OCEAN_REGION,
    DIGITAL_OCEAN_SECRET_KEY,
)

logger = logging.getLogger(__name__)


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
}

# Max file sizes per category (in bytes)
MAX_FILE_SIZES = {
    "image": 10 * 1024 * 1024,      # 10MB
    "video": 100 * 1024 * 1024,      # 100MB
    "application": 20 * 1024 * 1024,  # 20MB
}

# Presigned URL expiry (seconds)
PRESIGN_EXPIRY = 600  # 10 minutes


# >>>>>>>>>>>>>>>>>>>> Custom Exceptions <<<<<<<<<<<<<<<<<<<<<<

class InvalidFileType(Exception):
    pass

class FileTooLarge(Exception):
    pass

class StorageError(Exception):
    pass

class FileNotFound(Exception):
    pass


# >>>>>>>>>>>>>>>>>>>> S3 Client <<<<<<<<<<<<<<<<<<<<<<

def _get_s3_client():
    """
    Creates and returns a boto3 S3 client configured for DO Spaces.
    
    DO Spaces is S3-compatible, so we use the standard boto3 client 
    with the DO endpoint instead of AWS.
    """
    return boto3.client(
        "s3",
        region_name=DIGITAL_OCEAN_REGION,
        endpoint_url=DIGITAL_OCEAN_ENDPOINT,
        aws_access_key_id=DIGITAL_OCEAN_ACCESS_KEY,
        aws_secret_access_key=DIGITAL_OCEAN_SECRET_KEY,
        config=Config(signature_version="s3v4"),
    )


# >>>>>>>>>>>>>>>>>>>> Storage Service <<<<<<<<<<<<<<<<<<<<<<

class StorageService:
    """
    Centralized file storage service using DigitalOcean Spaces (S3-compatible).
    
    The flow:
        1. Frontend calls request_upload() to get a presigned PUT URL
        2. Frontend uploads the file directly to DO Spaces using that URL
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
    def request_upload(filename, content_type, folder="general"):
        """
        Generates a presigned PUT URL for direct upload to DO Spaces.
        
        Args:
            filename:     Original filename (used to extract extension)
            content_type: MIME type (e.g., "image/jpeg")
            folder:       Storage folder (e.g., "profiles", "certificates", "videos", "jobs", "chat")
        
        Returns:
            dict with:
                upload_url:  Presigned PUT URL (frontend uploads here)
                file_url:    CDN URL where the file will be accessible after upload
                file_key:    The S3 key (used for deletion later)
                expires_in:  Seconds until the presigned URL expires
        """
        
        # [1] Validate content type
        if content_type not in ALLOWED_CONTENT_TYPES:
            raise InvalidFileType(
                f"File type '{content_type}' is not allowed. "
                f"Allowed types: {', '.join(sorted(ALLOWED_CONTENT_TYPES))}"
            )
        
        # [2] Generate unique filename (prevents overwrites and name collisions)
        extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
        unique_name = f"{uuid.uuid4().hex}.{extension}"
        file_key = f"uploads/{folder}/{unique_name}"
        
        # [3] Generate presigned PUT URL
        try:
            client = _get_s3_client()
            
            upload_url = client.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": DIGITAL_OCEAN_BUCKET,
                    "Key": file_key,
                    "ContentType": content_type,
                    "ACL": "public-read",
                },
                ExpiresIn=PRESIGN_EXPIRY,
            )
        except ClientError as e:
            logger.error(f"[<>Storage<>] Presign failed: {e}")
            raise StorageError("Failed to generate upload URL. Please try again.")
        
        # [4] Build the CDN URL where the file will be accessible
        file_url = f"{DIGITAL_OCEAN_CDN_URL}/{file_key}"
        
        logger.info(f"[<>Storage<>] Upload URL generated: {file_key} ({content_type})")
        
        return {
            "upload_url": upload_url,
            "file_url": file_url,
            "file_key": file_key,
            "expires_in": PRESIGN_EXPIRY,
        }
    
    
    @staticmethod
    def upload_bytes(data, *, folder="general", content_type="image/jpeg", acl="private"):
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
                Bucket=DIGITAL_OCEAN_BUCKET,
                Key=file_key,
                Body=data,
                ContentType=content_type,
                ACL=acl,
            )
        except ClientError as e:
            logger.error(f"[<>Storage<>] upload_bytes failed for {file_key}: {e}")
            raise StorageError("Failed to store the file. Please try again.")

        logger.info(f"[<>Storage<>] Bytes uploaded: {file_key} ({content_type}, acl={acl})")
        return file_key

    @staticmethod
    def generate_presigned_get(file_key, expires_in=PRESIGN_EXPIRY):
        """Presigned GET URL for a (private) object, so the frontend can read it
        without the bucket being public. Accepts a raw key or a full CDN URL.
        Returns None on empty input or failure."""
        if not file_key:
            return None
        if file_key.startswith("http"):
            file_key = file_key.split(DIGITAL_OCEAN_CDN_URL + "/")[-1]

        try:
            client = _get_s3_client()
            return client.generate_presigned_url(
                "get_object",
                Params={"Bucket": DIGITAL_OCEAN_BUCKET, "Key": file_key},
                ExpiresIn=expires_in,
            )
        except ClientError as e:
            logger.error(f"[<>Storage<>] presigned GET failed for {file_key}: {e}")
            return None

    @staticmethod
    def delete_file(file_key):
        """
        Deletes a file from DO Spaces by its key.
        
        Args:
            file_key: The S3 key (e.g., "uploads/profiles/a1b2c3d4.jpg")
                       Can also accept a full CDN URL — it will extract the key.
        
        Returns:
            bool: True if deleted, False if failed
        """
        
        # Handle full URL input (extract key from CDN URL)
        if file_key.startswith("http"):
            # e.g., https://bucket.lon1.cdn.digitaloceanspaces.com/uploads/profiles/abc.jpg
            # Extract everything after the domain
            try:
                file_key = file_key.split(DIGITAL_OCEAN_CDN_URL + "/")[-1]
            except Exception:
                raise FileNotFound("Invalid file URL format.")
        
        try:
            client = _get_s3_client()
            
            client.delete_object(
                Bucket=DIGITAL_OCEAN_BUCKET,
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
        Checks if a file exists in DO Spaces.
        
        Returns:
            bool: True if exists, False otherwise
        """
        
        try:
            client = _get_s3_client()
            client.head_object(
                Bucket=DIGITAL_OCEAN_BUCKET,
                Key=file_key,
            )
            return True
            
        except ClientError:
            return False
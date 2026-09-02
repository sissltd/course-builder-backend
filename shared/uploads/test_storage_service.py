from unittest import TestCase
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from shared.services import storage_service


class StorageServiceTests(TestCase):
    def test_presign_and_file_url_use_endpoint_bucket_and_path_addressing(self):
        with (
            patch.object(storage_service, "DIGITAL_OCEAN_ACCESS_KEY", "access-key"),
            patch.object(storage_service, "DIGITAL_OCEAN_SECRET_KEY", "secret-key"),
            patch.object(
                storage_service,
                "DIGITAL_OCEAN_ENDPOINT",
                "https://storage.example.com/",
            ),
            patch.object(storage_service, "DIGITAL_OCEAN_BUCKET", "course-files"),
            patch.object(storage_service, "DIGITAL_OCEAN_REGION", "us-east-1"),
        ):
            result = storage_service.StorageService.request_upload(
                filename="lesson.mp4",
                content_type="video/mp4",
                folder="courses",
                size=1024,
                purpose="LESSON_VIDEO",
                width=1280,
                height=720,
                codec="h264",
            )

        upload_url = urlsplit(result["upload_url"])
        self.assertEqual(upload_url.scheme, "https")
        self.assertEqual(upload_url.netloc, "storage.example.com")
        self.assertTrue(upload_url.path.startswith("/course-files/uploads/courses/"))
        self.assertEqual(
            parse_qs(upload_url.query)["X-Amz-SignedHeaders"],
            [
                "content-length;content-type;host;x-amz-meta-codec;"
                "x-amz-meta-height;x-amz-meta-upload-purpose;x-amz-meta-width"
            ],
        )
        self.assertTrue(
            result["file_url"].startswith(
                "https://storage.example.com/course-files/uploads/courses/"
            )
        )
        self.assertEqual(result["upload_headers"]["Content-Type"], "video/mp4")
        self.assertEqual(result["upload_headers"]["x-amz-meta-codec"], "h264")

    def test_public_object_url_is_normalized_back_to_a_file_key(self):
        with patch.object(
            storage_service,
            "DIGITAL_OCEAN_BUCKET",
            "course-files",
        ):
            key = storage_service._file_key_from_value(
                "https://storage.example.com/course-files/uploads/courses/video.mp4"
            )

        self.assertEqual(key, "uploads/courses/video.mp4")

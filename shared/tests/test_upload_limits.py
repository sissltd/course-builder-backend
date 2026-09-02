"""Upload size and folder rules for the course builder's media uploads."""

from unittest.mock import patch

from django.test import SimpleTestCase

from shared.serializers.storage_serializer import UploadRequestSerializer
from shared.services.storage_service import (
    COURSE_UPLOAD_RULES,
    MAX_FILE_SIZES,
    FileTooLarge,
    InvalidFileType,
    InvalidUploadMetadata,
    StorageService,
    max_size_for,
)

MB = 1024 * 1024


class UploadLimitTests(SimpleTestCase):
    def test_video_cap_is_500mb(self):
        self.assertEqual(MAX_FILE_SIZES["video"], 500 * MB)
        self.assertEqual(
            COURSE_UPLOAD_RULES["COURSE_PREVIEW_VIDEO"]["max_size"], 100 * MB
        )
        self.assertEqual(COURSE_UPLOAD_RULES["COURSE_THUMBNAIL"]["max_size"], 5 * MB)

    def test_max_size_for_resolves_by_category(self):
        self.assertEqual(max_size_for("video/mp4"), 500 * MB)
        self.assertEqual(max_size_for("image/png"), 10 * MB)
        self.assertEqual(max_size_for("application/pdf"), 20 * MB)
        self.assertIsNone(max_size_for("audio/mpeg"))

    def test_oversized_video_is_refused_before_presigning(self):
        """The bug this guards: request_upload never checked size, so a
        presigned PUT was unbounded."""

        with self.assertRaises(FileTooLarge):
            StorageService.request_upload(
                filename="huge.mp4",
                content_type="video/mp4",
                folder="courses",
                size=501 * MB,
                purpose="LESSON_VIDEO",
                width=1280,
                height=720,
                codec="h264",
            )

    def test_oversized_image_is_refused(self):
        with self.assertRaises(FileTooLarge):
            StorageService.request_upload(
                filename="big.png",
                content_type="image/png",
                folder="thumbnails",
                size=6 * MB,
                purpose="COURSE_THUMBNAIL",
                width=1280,
                height=720,
            )

    def test_preview_video_has_100mb_and_two_minute_limits(self):
        common = {
            "filename": "preview.mp4",
            "content_type": "video/mp4",
            "folder": "courses",
            "purpose": "COURSE_PREVIEW_VIDEO",
            "width": 1280,
            "height": 720,
            "codec": "h264",
        }
        with self.assertRaises(FileTooLarge):
            StorageService.request_upload(**common, size=101 * MB, duration_seconds=90)
        with self.assertRaises(InvalidUploadMetadata):
            StorageService.request_upload(**common, size=50 * MB, duration_seconds=121)

    def test_thumbnail_requires_jpeg_or_png_at_16_by_9_and_720p(self):
        common = {
            "folder": "thumbnails",
            "size": MB,
            "purpose": "COURSE_THUMBNAIL",
        }
        with self.assertRaises(InvalidFileType):
            StorageService.request_upload(
                **common,
                filename="cover.webp",
                content_type="image/webp",
                width=1280,
                height=720,
            )
        with self.assertRaises(InvalidUploadMetadata):
            StorageService.request_upload(
                **common,
                filename="cover.png",
                content_type="image/png",
                width=1024,
                height=700,
            )

    def test_lesson_video_requires_h264_mp4_at_720p(self):
        common = {
            "folder": "courses",
            "size": 20 * MB,
            "purpose": "LESSON_VIDEO",
            "width": 1280,
            "height": 720,
        }
        with self.assertRaises(InvalidFileType):
            StorageService.request_upload(
                **common,
                filename="lesson.webm",
                content_type="video/webm",
                codec="h264",
            )
        with self.assertRaises(InvalidUploadMetadata):
            StorageService.request_upload(
                **common,
                filename="lesson.mp4",
                content_type="video/mp4",
                codec="vp9",
            )

    def test_srt_subtitles_are_supported(self):
        with patch("shared.services.storage_service._get_s3_client") as get_client:
            get_client.return_value.generate_presigned_url.return_value = (
                "https://storage.example/signed"
            )
            result = StorageService.request_upload(
                filename="lesson.srt",
                content_type="application/x-subrip",
                folder="courses",
                size=1024,
                purpose="SUBTITLE",
            )
        self.assertEqual(
            result["upload_headers"]["Content-Type"], "application/x-subrip"
        )

    def test_course_folder_requires_a_purpose(self):
        with self.assertRaises(InvalidUploadMetadata):
            StorageService.request_upload(
                filename="lesson.mp4",
                content_type="video/mp4",
                folder="courses",
                size=20 * MB,
            )


class UploadSerializerTests(SimpleTestCase):
    def test_course_upload_metadata_fields_are_accepted(self):
        serializer = UploadRequestSerializer(
            data={
                "filename": "clip.mp4",
                "content_type": "video/mp4",
                "folder": "courses",
                "size": 1000,
                "purpose": "LESSON_VIDEO",
                "width": 1920,
                "height": 1080,
                "codec": "h264",
            }
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_unknown_folder_is_refused(self):
        serializer = UploadRequestSerializer(
            data={
                "filename": "clip.mp4",
                "content_type": "video/mp4",
                "folder": "nowhere",
            }
        )

        self.assertFalse(serializer.is_valid())

    def test_size_is_optional(self):
        serializer = UploadRequestSerializer(
            data={"filename": "a.png", "content_type": "image/png"}
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertNotIn("size", serializer.validated_data)

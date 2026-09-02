"""Upload size and folder rules for the course builder's media uploads."""

from django.test import SimpleTestCase

from shared.serializers.storage_serializer import UploadRequestSerializer
from shared.services.storage_service import (
    MAX_FILE_SIZES,
    FileTooLarge,
    StorageService,
    max_size_for,
)

MB = 1024 * 1024


class UploadLimitTests(SimpleTestCase):
    def test_video_cap_is_500mb(self):
        self.assertEqual(MAX_FILE_SIZES["video"], 500 * MB)

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
            )

    def test_oversized_image_is_refused(self):
        with self.assertRaises(FileTooLarge):
            StorageService.request_upload(
                filename="big.png",
                content_type="image/png",
                folder="thumbnails",
                size=11 * MB,
            )


class UploadSerializerTests(SimpleTestCase):
    def test_builder_folders_are_accepted(self):
        for folder in ("courses", "thumbnails", "videos"):
            with self.subTest(folder=folder):
                serializer = UploadRequestSerializer(
                    data={
                        "filename": "clip.mp4",
                        "content_type": "video/mp4",
                        "folder": folder,
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

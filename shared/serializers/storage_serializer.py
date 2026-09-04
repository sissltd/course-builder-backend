from rest_framework import serializers

# >>>>>>>>>>>>>>>>>>>> Upload Request <<<<<<<<<<<<<<<<<<<<<<


class UploadRequestSerializer(serializers.Serializer):
    """
    Input payload for requesting a presigned upload URL.
    """

    filename = serializers.CharField(
        max_length=255, help_text="Original filename with extension (e.g., 'photo.jpg')"
    )
    content_type = serializers.CharField(
        max_length=100,
        help_text="MIME type of the file (e.g., 'image/jpeg', 'video/mp4')",
    )
    folder = serializers.ChoiceField(
        choices=[
            "profiles",
            "certificates",
            "videos",
            "courses",
            "thumbnails",
            "jobs",
            "chat",
            "quotations",
            "general",
        ],
        default="general",
        help_text=(
            "Storage folder — organizes files by purpose. Course builder "
            "uploads use 'thumbnails' for cover images and 'courses' for "
            "lesson media."
        ),
    )
    size = serializers.IntegerField(
        required=False,
        min_value=1,
        help_text=(
            "File size in bytes. Required for creator course media and signed "
            "into the PUT request; limits depend on purpose. Optional for "
            "legacy non-course uploads."
        ),
    )
    purpose = serializers.ChoiceField(
        choices=[
            "COURSE_THUMBNAIL",
            "LESSON_IMAGE",
            "LESSON_VIDEO",
            "COURSE_PREVIEW_VIDEO",
            "SUBTITLE",
        ],
        required=False,
        help_text="Required for creator course media; selects its PRD validation rules.",
    )
    width = serializers.IntegerField(
        required=False,
        min_value=1,
        help_text="Required pixel width for thumbnails and course videos.",
    )
    height = serializers.IntegerField(
        required=False,
        min_value=1,
        help_text="Required pixel height for thumbnails and course videos.",
    )
    duration_seconds = serializers.IntegerField(
        required=False,
        min_value=1,
        help_text="Required for course preview videos; must be 60–120 seconds.",
    )
    codec = serializers.CharField(
        required=False,
        max_length=30,
        help_text="Required for course videos; the PRD-supported value is h264.",
    )


class UploadResponseSerializer(serializers.Serializer):
    """
    Output — what the frontend needs to complete the upload.
    """

    upload_url = serializers.URLField(help_text="PUT this URL with the file bytes")
    upload_headers = serializers.DictField(
        child=serializers.CharField(),
        help_text="Headers that must be sent unchanged with the upload PUT request",
    )
    file_url = serializers.URLField(
        help_text=(
            "Temporary presigned GET URL for reading the file after upload; "
            "persist file_key so the backend can issue a fresh URL after it expires."
        )
    )
    file_key = serializers.CharField(
        help_text="File key — save this if you need to delete later"
    )
    expires_in = serializers.IntegerField(
        help_text="Seconds until the upload URL expires"
    )


class FileAccessRequestSerializer(serializers.Serializer):
    """Input for refreshing a private uploaded file's playback URL."""

    file_key = serializers.CharField(
        max_length=500,
        help_text="Durable file_key returned by the upload presign endpoint.",
    )


class FileAccessResponseSerializer(serializers.Serializer):
    """Temporary URL used by the frontend to read a private object."""

    file_url = serializers.URLField(
        help_text="Short-lived presigned GET URL for viewing or downloading the file."
    )
    expires_in = serializers.IntegerField(
        help_text="Seconds until the file URL expires; request a fresh URL afterward."
    )


# >>>>>>>>>>>>>>>>>>>> Delete Request <<<<<<<<<<<<<<<<<<<<<<


class DeleteFileSerializer(serializers.Serializer):
    """
    Input payload for deleting a file.
    """

    file_key = serializers.CharField(
        max_length=500,
        help_text="The file_key from the upload response, or the full CDN URL",
    )

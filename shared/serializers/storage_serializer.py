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
            "Optional file size in bytes. When supplied it is checked "
            "against the limit for the content type (images 10MB, videos "
            "500MB, PDFs 20MB) so an oversized file is rejected before the "
            "upload starts rather than after it fails."
        ),
    )


class UploadResponseSerializer(serializers.Serializer):
    """
    Output — what the frontend needs to complete the upload.
    """

    upload_url = serializers.URLField(help_text="PUT this URL with the file bytes")
    file_url = serializers.URLField(
        help_text="CDN URL where the file will be accessible after upload"
    )
    file_key = serializers.CharField(
        help_text="File key — save this if you need to delete later"
    )
    expires_in = serializers.IntegerField(
        help_text="Seconds until the upload URL expires"
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

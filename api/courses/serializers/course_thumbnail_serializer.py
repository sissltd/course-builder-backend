from rest_framework import serializers

from api.courses.enums import MediaSource
from api.courses.models import CourseThumbnail


class CourseThumbnailSerializer(serializers.ModelSerializer):
    """Representation of a course thumbnail.

    On write, either `file` (with source=UPLOAD) or `external_url` (with a
    non-UPLOAD source) must be supplied - never both, never neither; the
    model's check constraint enforces it at the DB layer too.
    """

    class Meta:
        model = CourseThumbnail
        fields = [
            "id",
            "course",
            "media_type",
            "source",
            "file",
            "external_url",
            "width",
            "height",
            "is_active",
            "created_datetime",
        ]
        read_only_fields = ["id", "course", "is_active", "created_datetime"]

    def validate(self, attrs):
        source = attrs.get("source", MediaSource.UPLOAD)
        file = attrs.get("file", "")
        external_url = attrs.get("external_url", "")

        if source == MediaSource.UPLOAD:
            if not file:
                raise serializers.ValidationError(
                    {"file": "An upload source requires a file path."}
                )
            if external_url:
                raise serializers.ValidationError(
                    "An upload cannot also carry an external_url."
                )
        else:
            if not external_url:
                raise serializers.ValidationError(
                    {"external_url": f"A {source} source requires an external_url."}
                )
            if file:
                raise serializers.ValidationError(
                    "An external source cannot also carry a file."
                )
        return attrs

from rest_framework import serializers


class CourseVideoDecisionSerializer(serializers.Serializer):
    """Body of `POST /courses/{id}/video-decision/`."""

    will_provide = serializers.BooleanField(
        help_text=(
            "True when the creator will add the video themselves; false to "
            "leave it to video production."
        )
    )

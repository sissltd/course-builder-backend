"""Request shape of the MIE video push.

With the platform reviewing text before video, a developer first pushes the
course text only, and sends the video after the text passes the first review
seat. The video arrives as the course's preview video plus a media reference
for each video lesson, addressed by the same `order` values the text push used.
"""

from rest_framework import serializers

from api.courses.constants import COURSE_MEDIA_URL_MAX_LENGTH
from api.mie.serializers.course_push_serializer import (
    MAX_PUSH_LESSONS_PER_MODULE,
    MAX_PUSH_MODULES,
)


class PushLessonVideoSerializer(serializers.Serializer):
    """One lesson's video, addressed by its module's and its own `order`."""

    module_order = serializers.IntegerField(
        min_value=1, help_text="The `order` of the lesson's module in the text push."
    )
    lesson_order = serializers.IntegerField(
        min_value=1, help_text="The lesson's own `order` within that module."
    )
    video_url = serializers.URLField(
        required=False,
        allow_blank=True,
        max_length=COURSE_MEDIA_URL_MAX_LENGTH,
        help_text="HTTPS URL of the lesson video.",
    )
    embedded_link = serializers.URLField(
        required=False,
        allow_blank=True,
        max_length=COURSE_MEDIA_URL_MAX_LENGTH,
        help_text="Link of an embeddable player, used instead of `video_url`.",
    )

    def validate(self, attrs):
        if not (attrs.get("video_url") or attrs.get("embedded_link")):
            raise serializers.ValidationError(
                "Give a video_url or an embedded_link for the lesson."
            )
        return attrs


class CourseVideoPushSerializer(serializers.Serializer):
    """The whole video: the preview video and each video lesson's media."""

    preview_video_url = serializers.URLField(
        max_length=COURSE_MEDIA_URL_MAX_LENGTH,
        help_text="HTTPS URL of the 1-2 minute course preview video (BR-015).",
    )
    lessons = PushLessonVideoSerializer(
        many=True,
        required=False,
        max_length=MAX_PUSH_MODULES * MAX_PUSH_LESSONS_PER_MODULE,
        help_text=(
            "Media for each VIDEO lesson. Every video lesson needs one before "
            "the course can be submitted."
        ),
    )

    def validate_lessons(self, value):
        addresses = [(item["module_order"], item["lesson_order"]) for item in value]
        if len(addresses) != len(set(addresses)):
            raise serializers.ValidationError(
                "Each lesson may appear once (module_order, lesson_order)."
            )
        return value

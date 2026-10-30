from rest_framework import serializers
from api.courses.constants import COURSE_MEDIA_URL_MAX_LENGTH
from api.mie.serializers.course_video_push_serializer import (
    CourseVideoPushSerializer,
    PushLessonVideoSerializer,
)


class StudioLessonVideoSerializer(PushLessonVideoSerializer):
    transcript = serializers.CharField(max_length=100000, trim_whitespace=False)
    captions_url = serializers.URLField(max_length=COURSE_MEDIA_URL_MAX_LENGTH)
    video_url = serializers.URLField(max_length=COURSE_MEDIA_URL_MAX_LENGTH)


class StudioVideoSerializer(CourseVideoPushSerializer):
    lessons = StudioLessonVideoSerializer(many=True, allow_empty=False, max_length=2500)


class StudioSourceLessonSerializer(serializers.Serializer):
    id = serializers.CharField(max_length=200)
    order = serializers.IntegerField(min_value=1)


class StudioSourceModuleSerializer(StudioSourceLessonSerializer):
    lessons = StudioSourceLessonSerializer(many=True, allow_empty=False, max_length=50)


class StudioReviewSerializer(serializers.Serializer):
    schema_version = serializers.ChoiceField(choices=["studio.review.v1"])
    submission_id = serializers.UUIDField()
    course_id = serializers.UUIDField()
    revision = serializers.IntegerField(min_value=1)
    kind = serializers.ChoiceField(choices=["text", "video"])
    content_hash = serializers.RegexField(r"^[a-f0-9]{64}$")
    content = serializers.DictField()
    source_map = StudioSourceModuleSerializer(
        many=True, allow_empty=False, max_length=50
    )


class StudioReviewReceiptSerializer(serializers.Serializer):
    accepted = serializers.BooleanField()
    receipt_id = serializers.UUIDField()
    course_id = serializers.UUIDField()
    revision = serializers.IntegerField()
    kind = serializers.CharField()
    content_hash = serializers.CharField()
    media_persisted = serializers.BooleanField()

from rest_framework import serializers
from api.courses.constants import COURSE_MEDIA_URL_MAX_LENGTH
from api.mie.serializers.course_push_serializer import CoursePushSerializer
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


class StudioTextReviewSchemaSerializer(StudioReviewSerializer):
    """The schema of a `kind: text` review with `content` spelled out.
    Documentation only: requests are validated by StudioReviewSerializer and
    studio_review_service.accept_review, which checks `content` with the
    same CoursePushSerializer."""

    kind = serializers.ChoiceField(choices=["text"])
    content = CoursePushSerializer(
        help_text=(
            "The whole course as the Course Builder editor writes it, every "
            "lesson TEXT and no video: the course fields, `modules` > "
            "`lessons` > `content_blocks`, a quiz per module, optional lesson "
            "quizzes and the `final_assessment`."
        )
    )


class StudioVideoReviewSchemaSerializer(StudioReviewSerializer):
    """The schema of a `kind: video` review with `content` spelled out.
    Documentation only, like StudioTextReviewSchemaSerializer."""

    kind = serializers.ChoiceField(choices=["video"])
    content = StudioVideoSerializer(
        help_text=(
            "The preview video and, for every lesson of the accepted text, "
            "its video, captions and transcript. Every URL is a `media_url` "
            "from POST /mie/v1/studio/uploads/."
        )
    )


class StudioReviewReceiptSerializer(serializers.Serializer):
    accepted = serializers.BooleanField()
    receipt_id = serializers.UUIDField()
    course_id = serializers.UUIDField()
    revision = serializers.IntegerField()
    kind = serializers.CharField()
    content_hash = serializers.CharField()
    media_persisted = serializers.BooleanField()

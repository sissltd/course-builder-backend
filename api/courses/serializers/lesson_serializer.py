from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from api.courses.enums import LessonContentType
from api.courses.models import (
    Lesson,
    LessonContentBlock,
    LessonImage,
    LessonRequirement,
)
from api.courses.serializers.assessment_serializer import AssessmentSerializer


class LessonContentBlockSerializer(serializers.ModelSerializer):
    """Representation of one block in a lesson's body editor."""

    class Meta:
        model = LessonContentBlock
        fields = [
            "id",
            "lesson",
            "order",
            "block_type",
            "text_content",
            "media_url",
            "quiz",
        ]
        read_only_fields = ["id", "lesson"]

    def validate(self, attrs):
        """A block's payload must match its type: prose blocks carry
        text_content, media blocks carry media_url, quiz blocks reference
        a quiz, and DIVIDER carries neither."""

        block_type = attrs.get("block_type") or getattr(
            self.instance, "block_type", None
        )
        text = attrs.get("text_content", getattr(self.instance, "text_content", ""))
        media = attrs.get("media_url", getattr(self.instance, "media_url", ""))
        quiz = attrs.get("quiz", getattr(self.instance, "quiz", None))

        text_blocks = {
            "HEADING_1",
            "HEADING_2",
            "PARAGRAPH",
            "NUMBERED_LIST",
            "BULLETED_LIST",
            "BLOCKQUOTE",
        }
        media_blocks = {"IMAGE", "VIDEO", "EMBED"}

        if block_type == "DIVIDER":
            if text or media or quiz:
                raise serializers.ValidationError(
                    "A DIVIDER block carries no content."
                )
        elif block_type == "QUIZ":
            if not quiz:
                raise serializers.ValidationError(
                    {"quiz": "A QUIZ block must reference a quiz."}
                )
            if text or media:
                raise serializers.ValidationError(
                    "A QUIZ block carries no text or media; set 'quiz' instead."
                )
        elif block_type in text_blocks:
            if not text:
                raise serializers.ValidationError(
                    {"text_content": f"A {block_type} block requires text_content."}
                )
            if media or quiz:
                raise serializers.ValidationError(
                    f"A {block_type} block must not set media_url or quiz."
                )
        elif block_type in media_blocks:
            if not media:
                raise serializers.ValidationError(
                    {"media_url": f"A {block_type} block requires media_url."}
                )
            if text or quiz:
                raise serializers.ValidationError(
                    f"A {block_type} block must not set text_content or quiz."
                )
        return attrs


class LessonImageSerializer(serializers.ModelSerializer):
    """Representation of one image in a lesson's media library."""

    class Meta:
        model = LessonImage
        fields = ["id", "lesson", "image", "caption", "source_type", "order"]
        read_only_fields = ["id", "lesson"]


class LessonRequirementSerializer(serializers.ModelSerializer):
    """Representation of one requirement line on a lesson."""

    class Meta:
        model = LessonRequirement
        fields = ["id", "lesson", "text", "order"]
        read_only_fields = ["id", "lesson"]


class LessonMiniSerializer(serializers.ModelSerializer):
    """Lightweight Lesson representation for use inside other nested payloads."""

    class Meta:
        model = Lesson
        fields = ["id", "title", "order"]
        read_only_fields = fields


class LessonSerializer(serializers.ModelSerializer):
    """Read-only representation of a Lesson, including its assessment if set."""

    assessment = serializers.SerializerMethodField()
    content_blocks = LessonContentBlockSerializer(many=True, read_only=True)
    images = LessonImageSerializer(many=True, read_only=True)
    requirements = LessonRequirementSerializer(many=True, read_only=True)

    class Meta:
        model = Lesson
        fields = [
            "id",
            "title",
            "order",
            "content_type",
            "script",
            "video_url",
            "embedded_link",
            "video_script_file",
            "learning_objectives",
            "duration_minutes",
            "assessment",
            "content_blocks",
            "images",
            "requirements",
        ]
        read_only_fields = fields

    @extend_schema_field(AssessmentSerializer(allow_null=True))
    def get_assessment(self, obj):
        # getattr with a default is safe here: Django's reverse one-to-one
        # descriptor raises an exception that also subclasses AttributeError,
        # so getattr(..., None) correctly returns None when unset.
        assessment = getattr(obj, "assessment", None)
        return AssessmentSerializer(assessment).data if assessment else None


class LessonWriteSerializer(serializers.ModelSerializer):
    """Create/update serializer for a Lesson.

    Validates only the shape of learning_objectives (a list of non-empty
    strings); the 2-5 count-per-lesson rule is centralized in
    quality_check_service and enforced at submit time. Includes read-only
    `id` so a client can immediately use it to set the lesson's assessment.
    """

    class Meta:
        model = Lesson
        fields = [
            "id",
            "title",
            "order",
            "content_type",
            "script",
            "video_url",
            "embedded_link",
            "video_script_file",
            "learning_objectives",
            "duration_minutes",
        ]
        read_only_fields = ["id"]

    def validate_learning_objectives(self, value):
        if not isinstance(value, list) or not all(
            isinstance(item, str) and item.strip() for item in value
        ):
            raise serializers.ValidationError(
                "learning_objectives must be a list of non-empty strings."
            )
        return value

    def validate(self, attrs):
        """A VIDEO lesson should carry at least one media reference; a QUIZ
        lesson is validated at submit time (assessment questions rule)."""

        content_type = attrs.get("content_type") or getattr(
            self.instance, "content_type", None
        )
        if content_type == LessonContentType.VIDEO:
            has_media = any(
                attrs.get(field, getattr(self.instance, field, ""))
                for field in ("video_url", "embedded_link")
            )
            if not has_media:
                raise serializers.ValidationError(
                    "A VIDEO lesson requires a video_url or embedded_link."
                )
        return attrs

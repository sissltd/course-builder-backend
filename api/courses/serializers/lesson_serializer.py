from drf_spectacular.utils import extend_schema_field, extend_schema_serializer
from rest_framework import serializers

from api.courses.enums import LessonContentType
from api.courses.models import (
    Lesson,
    LessonContentBlock,
    LessonImage,
    LessonRequirement,
)
from api.courses.models.lesson_requirement import LESSON_REQUIREMENT_MAX_LENGTH
from api.courses.serializers.assessment_serializer import AssessmentSerializer


_LESSON_REQUIREMENT_MAX_LENGTH_MESSAGE = (
    "Lesson Requirement must not exceed "
    f"{LESSON_REQUIREMENT_MAX_LENGTH} characters (it has {{length}})."
)


class LessonRequirementTextField(serializers.CharField):
    """CharField that reports the offending length when max_length is exceeded.

    DRF's default max_length failure cannot include the value's length, which
    leaves creators guessing how far over the limit they are.
    """

    default_error_messages = {
        "max_length": _LESSON_REQUIREMENT_MAX_LENGTH_MESSAGE,
    }

    def to_internal_value(self, data):
        # Mirror DRF's own trim-then-measure order so the reported length
        # matches the value DRF would store.
        if isinstance(data, str) and self.trim_whitespace:
            data = data.strip()
        if self.max_length is not None and len(data) > self.max_length:
            self.fail("max_length", max_length=self.max_length, length=len(data))
        return super().to_internal_value(data)



_LEARNING_OBJECTIVES_HELP_TEXT = (
    "JSON array of objective strings. Each array item is one complete objective; "
    "commas and other punctuation inside a string are preserved and do not create "
    "a new objective or line."
)


def _lesson_requirement_text(requirements: list[dict]) -> str:
    """Flatten legacy requirement rows into the single Figma editor value."""

    return "\n".join(
        requirement["text"] for requirement in requirements if requirement["text"]
    )


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
                raise serializers.ValidationError("A DIVIDER block carries no content.")
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

    text = LessonRequirementTextField(
        max_length=LESSON_REQUIREMENT_MAX_LENGTH,
        help_text=(
            "Lesson requirement text shown in the Figma editor. Internal line "
            "breaks and punctuation are preserved."
        ),
    )

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


@extend_schema_serializer(deprecate_fields=("content_type",))
class LessonSerializer(serializers.ModelSerializer):
    """Read-only representation of a Lesson, including its assessment if set."""

    lesson_type = serializers.ChoiceField(
        source="content_type",
        choices=LessonContentType.choices,
        read_only=True,
        help_text="Primary lesson type selected in the builder: VIDEO, QUIZ, or TEXT.",
    )
    content_type = serializers.ChoiceField(
        choices=LessonContentType.choices,
        read_only=True,
        help_text=(
            "Deprecated alias of lesson_type, retained temporarily for existing clients."
        ),
    )
    assessment = serializers.SerializerMethodField()
    content_blocks = LessonContentBlockSerializer(many=True, read_only=True)
    images = LessonImageSerializer(many=True, read_only=True)
    learning_objectives = serializers.ListField(
        child=serializers.CharField(),
        read_only=True,
        help_text=_LEARNING_OBJECTIVES_HELP_TEXT,
    )
    requirements = LessonRequirementSerializer(
        many=True,
        read_only=True,
        help_text=(
            "Ordered requirement rows retained for compatibility and granular "
            "editing through the dedicated requirements endpoints."
        ),
    )
    lesson_requirement = serializers.CharField(
        read_only=True,
        help_text=(
            "The single Lesson Requirement rich-text value shown in Figma. "
            "Line breaks and punctuation are preserved."
        ),
    )

    class Meta:
        model = Lesson
        fields = [
            "id",
            "title",
            "order",
            "lesson_type",
            "content_type",
            "script",
            "video_url",
            "embedded_link",
            "video_script_file",
            "learning_objectives",
            "duration_minutes",
            "lesson_requirement",
            "assessment",
            "content_blocks",
            "images",
            "requirements",
        ]
        read_only_fields = fields

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        representation["lesson_requirement"] = _lesson_requirement_text(
            representation["requirements"]
        )
        return representation

    @extend_schema_field(AssessmentSerializer(allow_null=True))
    def get_assessment(self, obj):
        # getattr with a default is safe here: Django's reverse one-to-one
        # descriptor raises an exception that also subclasses AttributeError,
        # so getattr(..., None) correctly returns None when unset.
        assessment = getattr(obj, "assessment", None)
        return AssessmentSerializer(assessment).data if assessment else None


@extend_schema_serializer(deprecate_fields=("content_type",))
class LessonWriteSerializer(serializers.ModelSerializer):
    """Create/update serializer for a Lesson.

    Validates only the shape of learning_objectives (a list of non-empty
    strings); the 2-5 count-per-lesson rule is centralized in
    quality_check_service and enforced at submit time. Includes read-only
    `id` so a client can immediately use it to set the lesson's assessment.
    """

    lesson_type = serializers.ChoiceField(
        source="content_type",
        choices=LessonContentType.choices,
        default=LessonContentType.TEXT,
        help_text=(
            "Primary lesson type selected in the builder: VIDEO, QUIZ, or TEXT. "
            "Defaults to TEXT when omitted by an older client."
        ),
    )
    content_type = serializers.ChoiceField(
        choices=LessonContentType.choices,
        required=False,
        help_text=(
            "Deprecated alias of lesson_type. When both fields are supplied, their "
            "values must match."
        ),
    )
    learning_objectives = serializers.ListField(
        child=serializers.CharField(allow_blank=False, trim_whitespace=True),
        required=False,
        help_text=_LEARNING_OBJECTIVES_HELP_TEXT,
    )
    lesson_requirement = LessonRequirementTextField(
        required=False,
        allow_blank=True,
        max_length=LESSON_REQUIREMENT_MAX_LENGTH,
        help_text=(
            "The single Lesson Requirement rich-text value shown in Figma. "
            "Internal line breaks, numbered-list text, and punctuation are "
            "preserved. Send an empty string to clear it."
        ),
    )
    requirements = LessonRequirementSerializer(
        many=True,
        required=False,
        help_text=(
            "Compatibility form for clients that edit ordered requirement rows. "
            "Use lesson_requirement for the single Figma editor value. Supplying "
            "either field replaces the lesson's current requirements."
        ),
    )

    class Meta:
        model = Lesson
        fields = [
            "id",
            "title",
            "order",
            "lesson_type",
            "content_type",
            "script",
            "video_url",
            "embedded_link",
            "video_script_file",
            "learning_objectives",
            "duration_minutes",
            "lesson_requirement",
            "requirements",
        ]
        read_only_fields = ["id"]

    def to_internal_value(self, data):
        lesson_type = data.get("lesson_type", serializers.empty)
        legacy_content_type = data.get("content_type", serializers.empty)
        if (
            lesson_type is not serializers.empty
            and legacy_content_type is not serializers.empty
            and lesson_type != legacy_content_type
        ):
            raise serializers.ValidationError(
                {
                    "lesson_type": (
                        "lesson_type and deprecated content_type must match when "
                        "both are supplied."
                    )
                }
            )
        if "lesson_requirement" in data and "requirements" in data:
            raise serializers.ValidationError(
                {
                    "lesson_requirement": (
                        "Send lesson_requirement or requirements, not both."
                    )
                }
            )
        return super().to_internal_value(data)

    def validate_learning_objectives(self, value):
        if not isinstance(value, list) or not all(
            isinstance(item, str) and item.strip() for item in value
        ):
            raise serializers.ValidationError(
                "learning_objectives must be a list of non-empty strings."
            )
        return value

    def validate_requirements(self, value):
        """Give omitted orders their list position and reject ambiguous ordering."""

        normalized = [
            {**requirement, "order": requirement.get("order", position)}
            for position, requirement in enumerate(value, start=1)
        ]
        orders = [requirement["order"] for requirement in normalized]
        if len(orders) != len(set(orders)):
            raise serializers.ValidationError(
                "Each lesson requirement must have a distinct order value."
            )
        return normalized

    def validate(self, attrs):
        """A VIDEO lesson should carry at least one media reference."""

        if "lesson_requirement" in attrs:
            lesson_requirement = attrs.pop("lesson_requirement")
            attrs["requirements"] = (
                [{"text": lesson_requirement, "order": 1}] if lesson_requirement else []
            )

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

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        representation["lesson_requirement"] = _lesson_requirement_text(
            representation["requirements"]
        )
        return representation

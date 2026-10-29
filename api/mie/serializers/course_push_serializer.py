"""Request and response shapes for the MIE course push.

A developer pushes a whole course in one body, in the same schema a Course
Creator builds in the course builder. Every level reuses the builder's own
write serializer - CourseCreateSerializer, ModuleWriteSerializer,
LessonWriteSerializer, LessonContentBlockSerializer, AssessmentWriteSerializer
- so field names, types and per-field rules cannot drift between the two
paths. What this module adds is only what nesting needs: the child lists,
list-position ordering when `order` is omitted, and caps on list length.

The caps are a request-size guardrail, set well above the platform's own
structural limits. Those limits (module counts, word counts, durations...)
live in PlatformSettings and are enforced by quality_check_service at
submit, exactly as for a creator.
"""

from drf_spectacular.utils import OpenApiExample, extend_schema_serializer
from rest_framework import serializers

from api.courses.enums import CourseStatus
from api.courses.models import LessonContentBlock
from api.courses.serializers.assessment_serializer import AssessmentWriteSerializer
from api.courses.serializers.course_serializer import CourseCreateSerializer
from api.courses.serializers.lesson_serializer import (
    LessonContentBlockSerializer,
    LessonWriteSerializer,
)
from api.courses.serializers.module_serializer import ModuleWriteSerializer

MAX_PUSH_MODULES = 50
"""Most modules one push may carry - a size guardrail, not a quality rule."""

MAX_PUSH_LESSONS_PER_MODULE = 50
"""Most lessons one pushed module may carry."""

MAX_PUSH_BLOCKS_PER_LESSON = 200
"""Most content blocks one pushed lesson may carry."""


def _with_positions(items: list[dict], label: str) -> list[dict]:
    """Give items without an `order` their 1-based list position, and refuse
    duplicate orders - the database would otherwise reject them mid-build."""

    ordered = [
        {**item, "order": position if item.get("order") is None else item["order"]}
        for position, item in enumerate(items, start=1)
    ]
    orders = [item["order"] for item in ordered]
    if len(orders) != len(set(orders)):
        raise serializers.ValidationError(f"Each {label} must have a distinct order.")
    return ordered


class PushContentBlockSerializer(LessonContentBlockSerializer):
    """One lesson body block. Same rules as the builder, minus QUIZ blocks:
    a QUIZ block points at an existing quiz row, which a pushed course does
    not have - a lesson quiz goes in the lesson's `assessment` instead."""

    class Meta(LessonContentBlockSerializer.Meta):
        fields = ["order", "block_type", "text_content", "media_url"]
        read_only_fields = []
        extra_kwargs = {
            "order": {
                "required": False,
                "help_text": "1-based position in the lesson. Defaults to the list position.",
            },
        }

    def validate_block_type(self, value):
        if value == LessonContentBlock.BlockType.QUIZ:
            raise serializers.ValidationError(
                "QUIZ blocks are not accepted on a course push. Put the "
                "lesson's quiz in its `assessment` instead."
            )
        return value


class PushLessonSerializer(LessonWriteSerializer):
    """One lesson: every LessonWriteSerializer field, plus its body blocks
    and an optional lesson quiz."""

    content_blocks = PushContentBlockSerializer(
        many=True,
        required=False,
        max_length=MAX_PUSH_BLOCKS_PER_LESSON,
        help_text=(
            "The lesson body, in order. Optional - `script` alone is enough "
            "for a TEXT lesson."
        ),
    )
    assessment = AssessmentWriteSerializer(
        required=False,
        help_text="Optional lesson quiz. Lesson quizzes have no question minimum.",
    )

    class Meta(LessonWriteSerializer.Meta):
        fields = [
            field for field in LessonWriteSerializer.Meta.fields if field != "id"
        ] + ["content_blocks", "assessment"]
        read_only_fields = []
        extra_kwargs = {
            "order": {
                "required": False,
                "help_text": "1-based position in the module. Defaults to the list position.",
            },
        }

    def validate_content_blocks(self, value):
        return _with_positions(value, "content block")


class PushModuleSerializer(ModuleWriteSerializer):
    """One module: every ModuleWriteSerializer field, plus its lessons and
    its module quiz."""

    lessons = PushLessonSerializer(
        many=True,
        allow_empty=False,
        max_length=MAX_PUSH_LESSONS_PER_MODULE,
        help_text="The module's lessons, in order.",
    )
    assessment = AssessmentWriteSerializer(
        required=False,
        help_text=(
            "The module quiz. Required before the course can be submitted - "
            "left out, the push fails the structural check."
        ),
    )

    class Meta(ModuleWriteSerializer.Meta):
        fields = [
            "title",
            "order",
            "description",
            "learning_objectives",
            "lessons",
            "assessment",
        ]
        read_only_fields = []
        extra_kwargs = {
            "order": {
                "required": False,
                "help_text": "1-based position in the course. Defaults to the list position.",
            },
        }

    def validate_lessons(self, value):
        return _with_positions(value, "lesson")


class CoursePushSerializer(CourseCreateSerializer):
    """The whole course: every CourseCreateSerializer field except `topic`,
    plus its modules and final assessment.

    `topic` is left out on purpose. Picking a topic reserves it for the
    course's owner, which is a creator-dashboard flow; a pushed course is
    placed by its idea's category and priced by category and difficulty.

    Validation only - course_push_service.push_course builds the course.
    """

    modules = PushModuleSerializer(
        many=True,
        allow_empty=False,
        max_length=MAX_PUSH_MODULES,
        help_text="The course's modules, in order.",
    )
    final_assessment = AssessmentWriteSerializer(
        required=False,
        help_text=(
            "The course's final assessment. Required before submission, with "
            "at least the platform's minimum number of questions."
        ),
    )

    class Meta(CourseCreateSerializer.Meta):
        fields = [
            field for field in CourseCreateSerializer.Meta.fields if field != "topic"
        ] + ["modules", "final_assessment"]
        extra_kwargs = {}

    def validate_modules(self, value):
        return _with_positions(value, "module")

    def create(self, validated_data):
        raise NotImplementedError("Use course_push_service.push_course.")


@extend_schema_serializer(
    examples=[
        OpenApiExample(
            "Course in review",
            value={
                "submission_id": "0d1c7b2e-6f5a-4a3f-9a2b-1f4e8c9d0a11",
                "submission_reference": "SCB-0d1c7b2e-A",
                "course_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
                "title": "Build a Production-Grade Rust Course",
                "status": CourseStatus.SUBMITTED,
                "module_count": 6,
                "lesson_count": 30,
                "submitted_at": "2026-09-30T10:00:00Z",
                "rejected_at": None,
                "published_at": None,
                "revision_feedback": None,
            },
            response_only=True,
        )
    ]
)
class DevCourseSerializer(serializers.Serializer):
    """Where the course a developer pushed for one idea stands."""

    submission_id = serializers.UUIDField(
        help_text="The approved idea this course belongs to."
    )
    submission_reference = serializers.CharField(
        help_text="The idea's public reference (suffix tracks the idea's status).",
    )
    course_id = serializers.UUIDField(help_text="The course's id.")
    title = serializers.CharField(
        help_text="Course title - always the approved idea title."
    )
    status = serializers.ChoiceField(
        choices=CourseStatus.choices,
        help_text=(
            "Where the course is in the creator review flow. DRAFT means a "
            "reviewer sent it back: read revision_feedback and push again."
        ),
    )
    module_count = serializers.IntegerField(help_text="Modules the course holds.")
    lesson_count = serializers.IntegerField(help_text="Lessons the course holds.")
    submitted_at = serializers.DateTimeField(
        allow_null=True, help_text="When the latest push was submitted for review."
    )
    rejected_at = serializers.DateTimeField(
        allow_null=True,
        help_text="When a reviewer last sent it back to DRAFT, if ever.",
    )
    published_at = serializers.DateTimeField(
        allow_null=True, help_text="When it was published, if it has been."
    )
    revision_feedback = serializers.JSONField(
        allow_null=True,
        help_text=(
            "Present only while the course is DRAFT after a rejection: the "
            "reviewer's feedback and the issues they flagged. Null otherwise."
        ),
    )

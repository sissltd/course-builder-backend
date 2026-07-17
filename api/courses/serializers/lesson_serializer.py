from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from api.courses.models import Lesson
from api.courses.serializers.assessment_serializer import AssessmentSerializer


class LessonMiniSerializer(serializers.ModelSerializer):
    """Lightweight Lesson representation for use inside other nested payloads."""

    class Meta:
        model = Lesson
        fields = ["id", "title", "order"]
        read_only_fields = fields


class LessonSerializer(serializers.ModelSerializer):
    """Read-only representation of a Lesson, including its assessment if set."""

    assessment = serializers.SerializerMethodField()

    class Meta:
        model = Lesson
        fields = [
            "id",
            "title",
            "order",
            "script",
            "learning_objectives",
            "duration_minutes",
            "assessment",
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
    course_validation_service and enforced at submit time. Includes read-only
    `id` so a client can immediately use it to set the lesson's assessment.
    """

    class Meta:
        model = Lesson
        fields = [
            "id",
            "title",
            "order",
            "script",
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

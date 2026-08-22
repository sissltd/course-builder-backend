from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from api.courses.models import Module
from api.courses.serializers.assessment_serializer import AssessmentSerializer
from api.courses.serializers.lesson_serializer import LessonSerializer


class ModuleMiniSerializer(serializers.ModelSerializer):
    """Lightweight Module representation for nesting inside other payloads
    (e.g. a collaborator's assigned_modules)."""

    class Meta:
        model = Module
        fields = ["id", "title", "order"]
        read_only_fields = fields


class ModuleSerializer(serializers.ModelSerializer):
    """Read-only representation of a Module, including its lessons and
    assessment, and current edit-lock state (SCCS PRD Section 14)."""

    lessons = LessonSerializer(many=True, read_only=True)
    assessment = serializers.SerializerMethodField()
    is_locked = serializers.BooleanField(read_only=True)

    class Meta:
        model = Module
        fields = [
            "id",
            "title",
            "order",
            "description",
            "learning_objectives",
            "lessons",
            "assessment",
            "locked_by",
            "lock_expires_at",
            "is_locked",
        ]
        read_only_fields = fields

    @extend_schema_field(AssessmentSerializer(allow_null=True))
    def get_assessment(self, obj):
        assessment = getattr(obj, "assessment", None)
        return AssessmentSerializer(assessment).data if assessment else None


class ModuleWriteSerializer(serializers.ModelSerializer):
    """Create/update serializer for a Module.

    Includes read-only `id` so a client creating a Module can immediately use
    it to create Lessons underneath, without a second GET round-trip.
    """

    class Meta:
        model = Module
        fields = ["id", "title", "order", "description", "learning_objectives"]
        read_only_fields = ["id"]

    def validate_learning_objectives(self, value):
        if not isinstance(value, list) or not all(
            isinstance(item, str) and item.strip() for item in value
        ):
            raise serializers.ValidationError(
                "learning_objectives must be a list of non-empty strings."
            )
        return value

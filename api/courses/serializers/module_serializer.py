from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from api.courses.models import Module
from api.courses.serializers.assessment_serializer import AssessmentSerializer
from api.courses.serializers.lesson_serializer import LessonSerializer


class ModuleSerializer(serializers.ModelSerializer):
    """Read-only representation of a Module, including its lessons and assessment."""

    lessons = LessonSerializer(many=True, read_only=True)
    assessment = serializers.SerializerMethodField()

    class Meta:
        model = Module
        fields = ["id", "title", "order", "lessons", "assessment"]
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
        fields = ["id", "title", "order"]
        read_only_fields = ["id"]

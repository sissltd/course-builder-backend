from rest_framework import serializers

from api.courses.models import Course, CourseAppeal
from api.courses.services import course_appeal_service


class AppealCourseMiniSerializer(serializers.ModelSerializer):
    """Lightweight Course representation for nesting inside CourseAppeal payloads."""

    class Meta:
        model = Course
        fields = ["id", "title", "status"]
        read_only_fields = fields


class CourseAppealSerializer(serializers.ModelSerializer):
    """Read-only representation of a CourseAppeal."""

    course = AppealCourseMiniSerializer(read_only=True)

    class Meta:
        model = CourseAppeal
        fields = [
            "id",
            "course",
            "title",
            "email",
            "web_link",
            "description",
            "status",
            "decision_notes",
            "reviewed_at",
            "created_datetime",
        ]
        read_only_fields = fields


class CourseAppealCreateSerializer(serializers.ModelSerializer):
    """Write serializer for a creator filing an appeal against a rejected course."""

    class Meta:
        model = CourseAppeal
        fields = ["course", "title", "email", "web_link", "description"]

    def create(self, validated_data):
        request = self.context["request"]
        return course_appeal_service.submit_appeal(
            user=request.user,
            course=validated_data["course"],
            title=validated_data["title"],
            email=validated_data["email"],
            web_link=validated_data.get("web_link", ""),
            description=validated_data["description"],
        )

    def to_representation(self, instance):
        return CourseAppealSerializer(instance, context=self.context).data


class CourseAppealDecisionSerializer(serializers.Serializer):
    """Request body shared by the approve/reject actions - optional free-text notes."""

    notes = serializers.CharField(required=False, allow_blank=True, default="")

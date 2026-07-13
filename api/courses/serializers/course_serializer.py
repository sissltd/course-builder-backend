from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from api.courses.models import Category, Course, ReviewAction
from api.courses.serializers.assessment_serializer import AssessmentSerializer
from api.courses.serializers.module_serializer import ModuleSerializer
from api.courses.services import course_service, course_validation_service


class CategoryMiniSerializer(serializers.ModelSerializer):
    """Lightweight Category representation for nesting inside Course payloads."""

    class Meta:
        model = Category
        fields = ["id", "name"]
        read_only_fields = fields


class CourseListSerializer(serializers.ModelSerializer):
    """Compact Course representation for list/queue views."""

    category = CategoryMiniSerializer(read_only=True)

    class Meta:
        model = Course
        fields = [
            "id",
            "title",
            "category",
            "status",
            "creator_price_snapshot",
            "submitted_at",
            "created_datetime",
        ]
        read_only_fields = fields


class CourseDetailSerializer(serializers.ModelSerializer):
    """Full Course representation, including nested modules/lessons/assessments."""

    category = CategoryMiniSerializer(read_only=True)
    modules = ModuleSerializer(many=True, read_only=True)
    final_assessment = serializers.SerializerMethodField()
    duration_estimate_minutes = serializers.SerializerMethodField()

    class Meta:
        model = Course
        fields = [
            "id",
            "title",
            "description",
            "category",
            "status",
            "creator_price_snapshot",
            "preview_video_url",
            "terms_accepted_at",
            "submitted_at",
            "approved_at",
            "published_at",
            "rejected_at",
            "modules",
            "final_assessment",
            "duration_estimate_minutes",
            "created_datetime",
            "updated_datetime",
        ]
        read_only_fields = fields

    @extend_schema_field(AssessmentSerializer(allow_null=True))
    def get_final_assessment(self, obj):
        assessment = getattr(obj, "final_assessment", None)
        return AssessmentSerializer(assessment).data if assessment else None

    @extend_schema_field(OpenApiTypes.INT)
    def get_duration_estimate_minutes(self, obj):
        return course_validation_service.get_course_duration_minutes(obj)


class CourseCreateSerializer(serializers.ModelSerializer):
    """Write serializer for creating a Draft course.

    Delegates persistence to course_service.create_draft_course so BR-005
    (terms acceptance) and category-status validation live in one place.
    """

    terms_accepted = serializers.BooleanField(write_only=True)

    class Meta:
        model = Course
        fields = [
            "category",
            "title",
            "description",
            "preview_video_url",
            "terms_accepted",
        ]

    def create(self, validated_data):
        request = self.context["request"]
        return course_service.create_draft_course(
            creator=request.user,
            category=validated_data["category"],
            title=validated_data["title"],
            description=validated_data["description"],
            preview_video_url=validated_data.get("preview_video_url", ""),
            terms_accepted=validated_data["terms_accepted"],
        )

    def to_representation(self, instance):
        return CourseDetailSerializer(instance, context=self.context).data


class CourseUpdateSerializer(serializers.ModelSerializer):
    """Write serializer for updating a Draft course.

    Delegates to course_service.update_draft_course, which enforces that only
    Draft courses can be edited.
    """

    class Meta:
        model = Course
        fields = ["title", "description", "preview_video_url", "category"]

    def update(self, instance, validated_data):
        request = self.context["request"]
        return course_service.update_draft_course(
            course=instance, actor=request.user, data=validated_data
        )

    def to_representation(self, instance):
        return CourseDetailSerializer(instance, context=self.context).data


class ReviewActionSerializer(serializers.ModelSerializer):
    """Read-only representation of a ReviewAction (audit record)."""

    reviewer = serializers.SerializerMethodField()

    class Meta:
        model = ReviewAction
        fields = ["id", "course", "reviewer", "action", "feedback", "created_datetime"]
        read_only_fields = fields

    def get_reviewer(self, obj):
        if not obj.reviewer_id:
            return None
        return {"id": obj.reviewer_id, "email": obj.reviewer.email}


class ReviewApproveSerializer(serializers.Serializer):
    """Request body for the review-queue approve action."""

    feedback = serializers.JSONField(required=False, default=dict)


class ReviewRejectSerializer(serializers.Serializer):
    """Request body for the review-queue reject action.

    Requires a non-empty feedback["summary"] (US-202: reviewers must leave
    detailed feedback the creator can act on).
    """

    feedback = serializers.JSONField(required=True)

    def validate_feedback(self, value):
        if not isinstance(value, dict) or not value.get("summary"):
            raise serializers.ValidationError("feedback.summary is required.")

        items = value.get("items", [])
        if not isinstance(items, list):
            raise serializers.ValidationError(
                "feedback.items must be a list if provided."
            )
        for index, item in enumerate(items):
            if (
                not isinstance(item, dict)
                or "module_id" not in item
                or "comment" not in item
            ):
                raise serializers.ValidationError(
                    f"feedback.items[{index}] must include 'module_id' and 'comment'."
                )

        return value

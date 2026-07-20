from rest_framework import serializers

from api.courses.enums import TrackPreference
from api.courses.models import CategoryRequest
from api.courses.serializers.course_serializer import CategoryMiniSerializer
from api.courses.services import category_request_service


class CategoryRequestSerializer(serializers.ModelSerializer):
    """Read-only representation of a CategoryRequest."""

    resulting_category = CategoryMiniSerializer(read_only=True)

    class Meta:
        model = CategoryRequest
        fields = [
            "id",
            "name",
            "status",
            "resulting_category",
            "reviewed_at",
            "created_datetime",
        ]
        read_only_fields = fields


class CategoryRequestCreateSerializer(serializers.ModelSerializer):
    """Write serializer for a creator submitting a new CategoryRequest."""

    class Meta:
        model = CategoryRequest
        fields = ["name"]

    def create(self, validated_data):
        request = self.context["request"]
        return category_request_service.submit_request(
            user=request.user, name=validated_data["name"]
        )

    def to_representation(self, instance):
        return CategoryRequestSerializer(instance, context=self.context).data


class CategoryRequestApproveSerializer(serializers.Serializer):
    """Request body for the admin-only approve action."""

    creator_price = serializers.DecimalField(max_digits=10, decimal_places=2)
    track_preference = serializers.ChoiceField(
        choices=TrackPreference.choices, required=False, default=TrackPreference.OPEN
    )

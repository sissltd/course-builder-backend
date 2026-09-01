from rest_framework import serializers

from api.catalog.models import CategoryRequest
from api.catalog.serializers.category_serializer import CategoryMiniSerializer
from api.catalog.services import category_request_service


class CategoryRequestSerializer(serializers.ModelSerializer):
    """Read-only representation of a creator's category request.

    `resulting_category` stays null until approval - the point of the flow
    is asking for a category that does not exist yet.
    """

    resulting_category = CategoryMiniSerializer(read_only=True)
    requested_by_email = serializers.EmailField(
        source="requested_by.email",
        read_only=True,
        help_text="Creator who filed the request.",
    )

    class Meta:
        model = CategoryRequest
        fields = [
            "id",
            "name",
            "description",
            "status",
            "resulting_category",
            "requested_by_email",
            "reviewed_at",
            "created_datetime",
        ]
        read_only_fields = fields


class CategoryRequestCreateSerializer(serializers.ModelSerializer):
    """Write serializer for a creator requesting a brand-new Category."""

    class Meta:
        model = CategoryRequest
        fields = ["name", "description"]
        extra_kwargs = {
            "name": {"help_text": "The category name being requested."},
            "description": {
                "required": False,
                "help_text": (
                    "Why it is needed and what belongs in it. Carried onto "
                    "the Category if the request is approved."
                ),
            },
        }

    def create(self, validated_data):
        request = self.context["request"]
        return category_request_service.submit_request(
            user=request.user,
            name=validated_data["name"],
            description=validated_data.get("description", ""),
        )

    def to_representation(self, instance):
        return CategoryRequestSerializer(instance, context=self.context).data


class CategoryRequestApproveSerializer(serializers.Serializer):
    """Request body for approving a category request.

    The price is supplied by the deciding admin rather than the requester:
    it determines what the platform pays a creator per approved course, so
    it is not the requester's to set.
    """

    creator_price = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=0,
        help_text="Fixed payout to a creator for an approved course in this category.",
    )
    track_preference = serializers.CharField(
        required=False,
        help_text="Optional production-track hint; defaults to the model's default.",
    )

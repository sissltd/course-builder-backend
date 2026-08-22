from typing import ClassVar

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from api.catalog.enums import CategoryStatus
from api.users.models import User


class MeSerializer(serializers.ModelSerializer):
    """Read-only representation of the current authenticated user."""

    has_completed_onboarding = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields: ClassVar = [
            "id",
            "email",
            "first_name",
            "last_name",
            "country",
            "state",
            "address",
            "phone_number",
            "timezone",
            "avatar_url",
            "terms_accepted_at",
            "role",
            "is_active",
            "status",
            "created_datetime",
            "updated_datetime",
            "has_completed_onboarding",
        ]
        read_only_fields = fields

    @extend_schema_field(OpenApiTypes.BOOL)
    def get_has_completed_onboarding(self, obj) -> bool:
        # Local import: keeps api.users -> api.onboarding a one-directional
        # dependency at runtime rather than a module-level import coupling
        # (api.onboarding.models never imports api.users at the top level).
        from api.onboarding.models import CreatorProfile

        return CreatorProfile.objects.filter(
            user=obj, onboarding_completed_at__isnull=False
        ).exists()

    def to_representation(self, instance):
        """Override the default representation to include the category field as a dict.
        The category is obtained from the CreatorProfile model. We keep the CreatorProfile as the only source of truth for category, to avoid confusion.
        """
        representation = super().to_representation(instance)
        if (
            hasattr(instance, "creator_profile")
            and instance.creator_profile is not None
            and instance.creator_profile.primary_expertise_category is not None
        ):
            representation["category"] = {
                "id": instance.creator_profile.primary_expertise_category.id,
                "name": instance.creator_profile.primary_expertise_category.name,
            }
        else:
            representation["category"] = None
        return representation


class MeUpdateSerializer(serializers.ModelSerializer):
    """Write serializer for PATCH /users/me/. Email is deliberately excluded -
    changing it needs re-verification, which has its own flow at
    /api/v1/auth/change-email/."""

    category = serializers.UUIDField(
        required=False,
        allow_null=True,
        help_text="ID of the category to associate with the user. Must be an active category.",
    )

    class Meta:
        model = User
        fields: ClassVar = [
            "first_name",
            "last_name",
            "timezone",
            "avatar_url",
            "phone_number",
            "country",
            "state",
            "address",
            "category",
        ]

    def save(self, **kwargs):
        """Override save to update the user's category based on the provided category ID.
        The category is stored in the CreatorProfile model, which is related to the User model.
        The CreatorProfile is the only source of truth for the category, so we update it directly.
        """
        category_id = self.validated_data.pop("category", None)
        if category_id is not None:
            from api.catalog.models import Category

            try:
                category = Category.objects.get(
                    id=category_id, status=CategoryStatus.ACTIVE
                )
                if (
                    hasattr(self.instance, "creator_profile")
                    and self.instance.creator_profile is not None
                ):
                    self.instance.creator_profile.primary_expertise_category = category
                    self.instance.creator_profile.save()
            except Category.DoesNotExist:
                raise serializers.ValidationError({"category": "Invalid category ID."})

        return super().save(**kwargs)

    def to_representation(self, instance):
        return MeSerializer(instance, context=self.context).data

from typing import ClassVar

from django.db import transaction
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from api.catalog.enums import CategoryStatus
from api.users.models import User


class ProfileCategorySerializer(serializers.Serializer):
    """Compact category representation embedded in a creator profile."""

    id = serializers.UUIDField(read_only=True, help_text="Active category ID.")
    name = serializers.CharField(read_only=True, help_text="Category display name.")


class ProfileBadgeSerializer(serializers.Serializer):
    """Swagger contract for badges awarded by a future badge engine."""

    code = serializers.CharField(
        read_only=True, help_text="Stable machine-readable badge code."
    )
    label = serializers.CharField(
        read_only=True, help_text="Human-readable badge label."
    )


class MeSerializer(serializers.ModelSerializer):
    """Read-only representation of the current authenticated user."""

    full_name = serializers.CharField(
        source="get_full_name",
        read_only=True,
        help_text="Display name composed from first and last name.",
    )
    member_since = serializers.DateTimeField(
        source="created_datetime",
        read_only=True,
        help_text="When the creator account was created.",
    )
    has_completed_onboarding = serializers.SerializerMethodField(
        help_text="Whether the creator completed every onboarding step."
    )
    is_verified = serializers.SerializerMethodField(
        help_text="Whether the creator's latest KYC submission is approved."
    )
    badges = serializers.SerializerMethodField(
        help_text="Badges awarded to the creator; empty until badge assignment is implemented."
    )
    category = ProfileCategorySerializer(
        source="creator_profile.primary_expertise_category",
        read_only=True,
        allow_null=True,
        help_text="Creator's selected active area-of-expertise category.",
    )

    class Meta:
        model = User
        fields: ClassVar = [
            "id",
            "email",
            "first_name",
            "last_name",
            "full_name",
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
            "member_since",
            "has_completed_onboarding",
            "is_verified",
            "badges",
            "category",
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

    @extend_schema_field(OpenApiTypes.BOOL)
    def get_is_verified(self, obj) -> bool:
        """Return the KYC verification state displayed by the profile UI."""

        from api.users.services import kyc_service

        return kyc_service.is_verified(user=obj)

    @extend_schema_field(ProfileBadgeSerializer(many=True))
    def get_badges(self, obj) -> list:
        """Return awarded profile badges.

        Badge assignment is not yet a domain capability, so do not infer or
        falsely award the Figma's illustrative ``Top creator`` badge.
        """

        return []


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

    def to_internal_value(self, data):
        """Reject email explicitly instead of silently ignoring it."""

        if "email" in data:
            raise serializers.ValidationError(
                {"email": "Email cannot be changed through this endpoint."}
            )
        return super().to_internal_value(data)

    def validate_category(self, category_id):
        """Resolve a supplied UUID to an active category; null means clear."""

        if category_id is None:
            return None

        from api.catalog.models import Category

        try:
            return Category.objects.get(id=category_id, status=CategoryStatus.ACTIVE)
        except Category.DoesNotExist:
            raise serializers.ValidationError("Invalid or inactive category ID.")

    @transaction.atomic
    def update(self, instance, validated_data):
        """Atomically update User fields and the CreatorProfile category."""

        category_was_supplied = "category" in validated_data
        category = validated_data.pop("category", None)
        user = super().update(instance, validated_data)

        if category_was_supplied:
            from api.onboarding.services import creator_profile_service

            profile = creator_profile_service.get_or_create_profile(user=user)
            profile.primary_expertise_category = category
            profile.save(
                update_fields=["primary_expertise_category", "updated_datetime"]
            )
            # A reverse one-to-one profile may already be cached on the User
            # instance (for example when the caller loaded it before PATCH).
            # Drop it so the response cannot serialize the pre-update category.
            user._state.fields_cache.pop("creator_profile", None)

        return user

    def to_representation(self, instance):
        return MeSerializer(instance, context=self.context).data

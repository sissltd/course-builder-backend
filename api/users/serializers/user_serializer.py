from typing import ClassVar

from django.db import transaction
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from api.catalog.enums import CategoryStatus
from api.users.enums import UserRole
from api.users.models import User


class ProfileCategorySerializer(serializers.Serializer):
    """Compact category representation embedded in a creator profile."""

    id = serializers.UUIDField(read_only=True, help_text="Active category ID.")
    name = serializers.CharField(read_only=True, help_text="Category display name.")


class ProfileBadgeSerializer(serializers.Serializer):
    """A badge the user holds, as the profile draws it.

    `code` and `label` were the original placeholder contract and keep their
    meaning; the rest were added when badges became real (api.achievements).
    """

    code = serializers.CharField(
        source="badge.id",
        read_only=True,
        help_text="Stable machine-readable badge code (the badge id).",
    )
    label = serializers.CharField(
        source="badge.title", read_only=True, help_text="Human-readable badge label."
    )
    icon = serializers.CharField(
        source="badge.icon", read_only=True, help_text="Icon identifier."
    )
    color = serializers.CharField(
        source="badge.color", read_only=True, help_text="Badge colour as #RRGGBB."
    )
    awarded_at = serializers.DateTimeField(
        read_only=True, help_text="When the badge was awarded."
    )


class ProfileAccessRoleSerializer(serializers.Serializer):
    """The role whose permissions the user holds."""

    id = serializers.UUIDField(read_only=True, help_text="Access role id.")
    name = serializers.CharField(read_only=True, help_text="Role name.")
    is_system = serializers.SerializerMethodField(
        help_text="True for built-in roles, false for custom roles."
    )
    base_role = serializers.CharField(
        read_only=True, help_text="The built-in role whose workflow the user follows."
    )

    def get_is_system(self, obj) -> bool:
        return obj.system_key is not None


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
    role_label = serializers.SerializerMethodField(
        help_text="Display name for the role, e.g. `Writer`. Safe to render as-is."
    )
    badges = serializers.SerializerMethodField(
        help_text="Achievement badges the user currently holds, newest first."
    )
    access_role = ProfileAccessRoleSerializer(
        read_only=True, help_text="The role whose permissions the user holds."
    )
    permissions = serializers.SerializerMethodField(
        help_text=(
            "Permission codenames the user holds, sorted. Use these to decide "
            "which controls to show; the API enforces them regardless."
        )
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
            "role_label",
            "access_role",
            "permissions",
            "assigned_track",
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

        from api.users.services.kyc_services import kyc_submission_service

        return kyc_submission_service.is_verified(user=obj)

    def get_role_label(self, obj) -> str:
        return UserRole(obj.role).label

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_permissions(self, obj) -> list:
        from api.authorization.services import permission_service

        return sorted(permission_service.get_permissions(obj))

    @extend_schema_field(ProfileBadgeSerializer(many=True))
    def get_badges(self, obj) -> list:
        """Return the badges the user holds. One query."""

        from api.achievements.services import award_service

        return ProfileBadgeSerializer(
            award_service.held_badges(user=obj), many=True
        ).data


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

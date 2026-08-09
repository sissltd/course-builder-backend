from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from api.users.models import User


class MeSerializer(serializers.ModelSerializer):
    """Read-only representation of the current authenticated user."""

    has_completed_onboarding = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "first_name",
            "last_name",
            "country",
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


class MeUpdateSerializer(serializers.ModelSerializer):
    """Write serializer for PATCH /users/me/. Email is deliberately excluded -
    changing it needs re-verification, which has its own flow at
    /api/v1/auth/change-email/."""

    class Meta:
        model = User
        fields = ["first_name", "last_name", "timezone", "avatar_url"]

    def to_representation(self, instance):
        return MeSerializer(instance, context=self.context).data

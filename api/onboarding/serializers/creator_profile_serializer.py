from rest_framework import serializers

from api.courses.serializers.course_serializer import CategoryMiniSerializer
from api.onboarding.models import CreatorProfile


class CreatorProfileSerializer(serializers.ModelSerializer):
    """Read-only representation of the current user's onboarding profile."""

    primary_expertise_category = CategoryMiniSerializer(read_only=True)
    has_completed_onboarding = serializers.BooleanField(read_only=True)

    class Meta:
        model = CreatorProfile
        fields = [
            "id",
            "primary_expertise_category",
            "primary_expertise_other",
            "video_comfort_level",
            "monthly_course_capacity",
            "agreement_accepted_at",
            "onboarding_completed_at",
            "has_completed_onboarding",
        ]
        read_only_fields = fields

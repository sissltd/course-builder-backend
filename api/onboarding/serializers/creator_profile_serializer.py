from rest_framework import serializers

from api.onboarding.models import CreatorProfile


class CreatorProfileSerializer(serializers.ModelSerializer):
    """Read-only representation of the current user's onboarding profile."""

    has_completed_onboarding = serializers.BooleanField(read_only=True)
    needs_policy_reacceptance = serializers.BooleanField(read_only=True)
    primary_expertise_category = serializers.PrimaryKeyRelatedField(
        read_only=True,
    )

    class Meta:
        model = CreatorProfile
        fields = [
            "id",
            "primary_expertise_category",
            "primary_expertise_area",
            "primary_expertise_other",
            "video_comfort_level",
            "monthly_course_capacity",
            "agreement_accepted_at",
            "agreement_accepted_version",
            "onboarding_completed_at",
            "has_completed_onboarding",
            "needs_policy_reacceptance",
        ]
        read_only_fields = fields

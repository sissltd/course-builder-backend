from rest_framework import serializers

from api.categories.enums import CategoryStatus
from api.categories.models import Category
from api.onboarding.enums import ExpertiseArea, MonthlyCourseCapacity, VideoComfortLevel


class OnboardingUpdateSerializer(serializers.Serializer):
    """Write serializer for PATCH /users/me/onboarding/.

    Every field is optional so a client can call this once per wizard step
    with just that step's field(s). At least one field must be provided.
    """

    category_id = serializers.PrimaryKeyRelatedField(
        required=False,
        queryset=Category.objects.filter(status=CategoryStatus.ACTIVE),
        help_text=(
            "Step 1: id of the creator's primary area-of-expertise Category. "
            "Must currently be ACTIVE."
        ),
    )
    expertise_area = serializers.ChoiceField(
        choices=ExpertiseArea.choices,
        required=False,
        help_text="Step 1: fixed-choice primary area of expertise.",
    )
    other_expertise = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=255,
        help_text=(
            "Free-text expertise description, required when expertise_area "
            "is 'OTHERS'."
        ),
    )
    video_comfort_level = serializers.ChoiceField(
        choices=VideoComfortLevel.choices,
        required=False,
        help_text="Step 2: self-reported comfort producing video content.",
    )
    monthly_course_capacity = serializers.ChoiceField(
        choices=MonthlyCourseCapacity.choices,
        required=False,
        help_text="Step 3: self-estimated number of courses producible per month.",
    )
    agreement_accepted = serializers.BooleanField(
        required=False,
        help_text=(
            "Step 4 (final step): pass true to accept the creator agreement. "
            "The first time this is sent, it also completes onboarding and "
            "unlocks Course Builder access. Sending it again later "
            "re-accepts the agreement at whatever policy version is "
            "currently in effect - required if the platform's "
            "creator_agreement_policy_version has changed since the "
            "creator's last acceptance (see needs_policy_reacceptance on "
            "GET), without resetting when onboarding was first completed."
        ),
    )

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError(
                "At least one onboarding field must be provided."
            )
        if (
            attrs.get("expertise_area") == ExpertiseArea.OTHERS
            and not attrs.get("other_expertise", "").strip()
        ):
            raise serializers.ValidationError(
                "other_expertise is required when expertise_area is 'Others'."
            )
        return attrs

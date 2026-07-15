from rest_framework import serializers

from api.onboarding.enums import ExpertiseArea, MonthlyCourseCapacity, VideoComfortLevel


class OnboardingUpdateSerializer(serializers.Serializer):
    """Write serializer for PATCH /users/me/onboarding/.

    Every field is optional so a client can call this once per wizard step
    with just that step's field(s). At least one field must be provided.
    """

    expertise_area = serializers.ChoiceField(
        choices=ExpertiseArea.choices, required=False
    )
    other_expertise = serializers.CharField(
        required=False, allow_blank=True, max_length=255
    )
    video_comfort_level = serializers.ChoiceField(
        choices=VideoComfortLevel.choices, required=False
    )
    monthly_course_capacity = serializers.ChoiceField(
        choices=MonthlyCourseCapacity.choices, required=False
    )
    agreement_accepted = serializers.BooleanField(required=False)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError(
                "At least one onboarding field must be provided."
            )
        if attrs.get("expertise_area") == ExpertiseArea.OTHERS and not attrs.get(
            "other_expertise", ""
        ).strip():
            raise serializers.ValidationError(
                "other_expertise is required when expertise_area is 'Others'."
            )
        return attrs

from rest_framework import serializers

from api.courses.enums import CategoryStatus
from api.courses.models import Category
from api.onboarding.enums import MonthlyCourseCapacity, VideoComfortLevel


class OnboardingUpdateSerializer(serializers.Serializer):
    """Write serializer for PATCH /users/me/onboarding/.

    Every field is optional so a client can call this once per wizard step
    with just that step's field(s). At least one field must be provided.
    """

    category_id = serializers.PrimaryKeyRelatedField(
        required=False,
        queryset=Category.objects.filter(status=CategoryStatus.ACTIVE),
    )
    other_expertise = serializers.CharField(required=False, allow_blank=True, max_length=255)
    video_comfort_level = serializers.ChoiceField(choices=VideoComfortLevel.choices, required=False)
    monthly_course_capacity = serializers.ChoiceField(choices=MonthlyCourseCapacity.choices, required=False)
    agreement_accepted = serializers.BooleanField(required=False)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("At least one onboarding field must be provided.")
        return attrs

from rest_framework import serializers

from api.users.enums import UnavailabilityReason
from api.users.models import ReviewerAvailability


class ReviewerAvailabilitySerializer(serializers.ModelSerializer):
    """Read-only representation of the current reviewer's availability."""

    is_effectively_available = serializers.BooleanField(read_only=True)

    class Meta:
        model = ReviewerAvailability
        fields = [
            "id",
            "is_available",
            "unavailability_reason",
            "return_date",
            "auto_return_enabled",
            "is_effectively_available",
        ]
        read_only_fields = fields


class ReviewerAvailabilityUpdateSerializer(serializers.Serializer):
    """Write serializer for PATCH /users/me/availability/. All fields optional."""

    is_available = serializers.BooleanField(required=False)
    unavailability_reason = serializers.ChoiceField(
        choices=UnavailabilityReason.choices, required=False, allow_blank=True
    )
    return_date = serializers.DateField(required=False, allow_null=True)
    auto_return_enabled = serializers.BooleanField(required=False)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError(
                "At least one availability field must be provided."
            )
        return attrs

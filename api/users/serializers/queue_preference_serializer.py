from rest_framework import serializers

from api.users.enums import QueueSortOrder, QueueTrackFilter
from api.users.models import QueueBehaviourPreference


class QueueBehaviourPreferenceSerializer(serializers.ModelSerializer):
    """Read-only representation of the current reviewer's queue-behaviour
    preference."""

    class Meta:
        model = QueueBehaviourPreference
        fields = [
            "id",
            "default_sort_order",
            "auto_advance_enabled",
            "track_filter",
        ]
        read_only_fields = fields


class QueueBehaviourPreferenceUpdateSerializer(serializers.Serializer):
    """Write serializer for PATCH /users/me/queue-preferences/. All fields
    optional."""

    default_sort_order = serializers.ChoiceField(
        choices=QueueSortOrder.choices, required=False
    )
    auto_advance_enabled = serializers.BooleanField(required=False)
    track_filter = serializers.ChoiceField(
        choices=QueueTrackFilter.choices, required=False
    )

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError(
                "At least one queue-behaviour field must be provided."
            )
        return attrs

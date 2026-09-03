from rest_framework import serializers

from api.users.enums import QueueSortOrder
from api.users.models import QueueBehaviourPreference


class QueueBehaviourPreferenceSerializer(serializers.ModelSerializer):
    """Read-only representation of the current reviewer's queue-behaviour
    preference.

    `effective_track_filter` is derived, not stored: the screen shows three
    independent toggles, and this is the single narrowing they resolve to.
    Exposed so the client can show what the queue will actually contain
    without re-deriving the rule.
    """

    effective_track_filter = serializers.CharField(
        read_only=True,
        help_text=(
            "Narrowing the three toggles resolve to: ALL, AI_TRACK, "
            "CREATOR_TRACK, or NONE. NONE means every track is switched "
            "off and the queue is intentionally empty."
        ),
    )

    class Meta:
        model = QueueBehaviourPreference
        fields = [
            "id",
            "default_sort_order",
            "auto_advance_enabled",
            "show_ai_track",
            "show_creator_track",
            "show_both_track",
            "effective_track_filter",
        ]
        read_only_fields = fields


class QueueBehaviourPreferenceUpdateSerializer(serializers.Serializer):
    """Write serializer for PATCH /users/me/queue-preferences/. All fields
    optional."""

    default_sort_order = serializers.ChoiceField(
        choices=QueueSortOrder.choices,
        required=False,
        help_text=(
            "Default queue view. ALL and OLDEST_FIRST are unfiltered "
            "oldest-first; NEWEST_FIRST reverses it; LAST_30_DAYS, "
            "LAST_7_DAYS and LAST_24_HOURS narrow to that window and sort "
            "oldest-first."
        ),
    )
    auto_advance_enabled = serializers.BooleanField(
        required=False,
        help_text=(
            "Frontend-only: load the next course immediately after a "
            "decision. Stored here for round-trip; it has no backend effect."
        ),
    )
    show_ai_track = serializers.BooleanField(
        required=False, help_text="Include APE-produced courses in the queue."
    )
    show_creator_track = serializers.BooleanField(
        required=False, help_text="Include human-submitted courses in the queue."
    )
    show_both_track = serializers.BooleanField(
        required=False,
        help_text=(
            "Include both tracks. Wins over the two toggles above when on. "
            "Switching all three off yields an intentionally empty queue."
        ),
    )

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError(
                "At least one queue-behaviour field must be provided."
            )
        return attrs

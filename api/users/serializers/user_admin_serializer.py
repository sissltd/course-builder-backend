from django.utils import timezone
from rest_framework import serializers

from api.users.enums import QueueTrackFilter
from api.users.models import User


class UserAdminSerializer(serializers.ModelSerializer):
    """Read-only representation of a user account for the admin roster.

    Wider than MeSerializer (which is the account holder's own view): it adds
    the moderation fields an Admin decides on - lockout state and the last
    login - without exposing anything the account holder could not already see
    about themselves.
    """

    role_label = serializers.CharField(
        source="get_role_display",
        read_only=True,
        help_text="Human-readable role name, e.g. 'Course Creator'.",
    )
    status_label = serializers.CharField(
        source="get_status_display",
        read_only=True,
        help_text="Human-readable account status, e.g. 'Suspended'.",
    )
    is_locked = serializers.SerializerMethodField(
        help_text="True while the account is locked out after repeated failed logins."
    )

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "first_name",
            "last_name",
            "role",
            "assigned_track",
            "role_label",
            "status",
            "status_label",
            "is_active",
            "is_locked",
            "country",
            "last_login",
            "created_datetime",
        ]
        read_only_fields = fields

    def get_is_locked(self, obj) -> bool:
        return bool(obj.locked_until and obj.locked_until > timezone.now())


class UserSuspendSerializer(serializers.Serializer):
    """Request body for the admin roster's suspend and deactivate actions.

    A reason is mandatory on both: the account holder is told why (suspension
    raises an in-app notification quoting it) and it is recorded on the
    actioning admin's activity log, so a moderation decision is never
    anonymous or unexplained.
    """

    reason = serializers.CharField(
        max_length=255,
        help_text=(
            "Why the account is being actioned. Shown to the account holder "
            "and stored on the admin's activity log. Must not be blank."
        ),
    )

    def validate_reason(self, value: str) -> str:
        if not value.strip():
            raise serializers.ValidationError("reason must not be empty.")
        return value


class UserReinstateSerializer(serializers.Serializer):
    """Request body for the admin roster's reinstate action. No fields
    required - restoring an account needs no accompanying data."""


class UserAssignTrackSerializer(serializers.Serializer):
    """Request body for assigning a reviewer to a production track.

    Null clears the assignment. The reviewer sees this on their Account
    settings screen as read-only - their own queue filter is a separate,
    self-service preference.
    """

    assigned_track = serializers.ChoiceField(
        choices=QueueTrackFilter.choices,
        allow_null=True,
        help_text=(
            "Track to assign, or null to clear it. Distinct from the "
            "reviewer's own queue track filter."
        ),
    )

"""Response-shape serializers used purely for OpenAPI schema generation.

The swagger standard requires every documented response to name a serializer so
drf-spectacular can render the shape, but several auth views build their payload
inline (tokens + nested user) rather than from a ModelSerializer. These classes
describe those inline shapes. They are never used to parse or validate input.
"""

from rest_framework import serializers

from api.authentication.serializers.staff_invitation_serializer import (
    StaffMemberSerializer,
)
from api.users.serializers import MeSerializer


class AuthTokenPairResponseSerializer(serializers.Serializer):
    """A JWT access/refresh pair plus the authenticated user."""

    access = serializers.CharField(
        help_text="Short-lived JWT access token. Send as `Authorization: Bearer <access>`."
    )
    refresh = serializers.CharField(
        help_text=(
            "Long-lived refresh token. Exchange at /api/v1/auth/token/refresh/ "
            "for a new access token."
        )
    )
    user = MeSerializer(help_text="The authenticated user's profile.")


class StaffInvitationCreatedResponseSerializer(serializers.Serializer):
    """Confirmation that a staff invitation was created and emailed."""

    detail = serializers.CharField(
        help_text="Human-readable confirmation suitable for display in a toast."
    )
    staff = StaffMemberSerializer(
        help_text=(
            "The pending staff account, ready to append to the Teams list. "
            "`invitation_status` is `PENDING` until the invitee accepts."
        )
    )


class StaffActionResponseSerializer(serializers.Serializer):
    """Confirmation that a staff member was revoked or reactivated."""

    detail = serializers.CharField(
        help_text="Human-readable confirmation suitable for display in a toast."
    )
    staff = StaffMemberSerializer(
        help_text="The staff member's updated row, with the new `invitation_status`."
    )

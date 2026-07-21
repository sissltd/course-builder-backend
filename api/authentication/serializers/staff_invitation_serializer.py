from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from api.users.enums import INVITABLE_STAFF_ROLES, UserRole


class StaffInvitationSerializer(serializers.Serializer):
    """Super Admin input for inviting a new staff member.

    No password field: the invitee sets their own password when they accept, so
    a Super Admin never knows another operator's credentials.
    """

    email = serializers.EmailField(
        help_text=(
            "Work email of the person being invited. The invitation link is "
            "sent here, and this becomes their login email."
        ),
    )
    first_name = serializers.CharField(
        max_length=150,
        help_text="Given name of the invitee. Used to address the invitation email.",
    )
    last_name = serializers.CharField(
        max_length=150,
        help_text="Family name of the invitee.",
    )
    role = serializers.ChoiceField(
        # Restricted to the three invitable positions rather than UserRole at
        # large: this is the boundary that stops a Super Admin from minting a
        # second Super Admin, or an invitee from landing on a public role.
        choices=[(role.value, role.label) for role in INVITABLE_STAFF_ROLES],
        help_text=(
            "Staff position to grant on acceptance. One of "
            "`STAFF_WRITER` (Writer - authors courses), "
            "`STAFF_VERIFIER` (Verifier - reviews submitted courses), or "
            "`STAFF_APPROVER` (Approver - approves and publishes courses). "
            "Any other role, including SUPER_ADMIN, is rejected."
        ),
    )


class AcceptStaffInvitationSerializer(serializers.Serializer):
    """Invitee input for accepting a staff invitation and setting a password."""

    email = serializers.EmailField(
        help_text=(
            "The invited email address. Must match the `email` query parameter "
            "on the invitation link exactly."
        ),
    )
    token = serializers.CharField(
        help_text=(
            "Raw invitation token from the `token` query parameter on the "
            "invitation link. Single-use and expires - request a fresh "
            "invitation from the Super Admin if it lapses."
        ),
    )
    password = serializers.CharField(
        write_only=True,
        validators=[validate_password],
        help_text=(
            "Password the invitee is setting for their staff account. Must "
            "pass Django's configured password validators. This becomes their "
            "permanent login credential."
        ),
    )


class StaffMemberSerializer(serializers.Serializer):
    """A staff member as shown on the Teams page.

    Read-only projection of User. Deliberately omits everything not needed to
    render a team roster - no password state, no permission flags.
    """

    id = serializers.UUIDField(read_only=True, help_text="Staff member's user ID.")
    email = serializers.EmailField(read_only=True, help_text="Login email.")
    first_name = serializers.CharField(read_only=True, help_text="Given name.")
    last_name = serializers.CharField(read_only=True, help_text="Family name.")
    role = serializers.CharField(
        read_only=True, help_text="Raw role value, e.g. `STAFF_WRITER`."
    )
    role_label = serializers.SerializerMethodField(
        help_text="Display name for the role, e.g. `Writer`. Safe to render as-is."
    )
    invitation_status = serializers.SerializerMethodField(
        help_text=(
            "One of `PENDING` (invited, never accepted), `ACTIVE` (accepted "
            "and able to log in), or `REVOKED` (access withdrawn). Drives the "
            "status pill and which actions the row offers."
        )
    )
    invited_by = serializers.SerializerMethodField(
        help_text="Email of the Super Admin who issued the invitation, if known."
    )
    created_datetime = serializers.DateTimeField(
        read_only=True, help_text="When the invitation was issued."
    )

    def get_role_label(self, obj) -> str:
        return UserRole(obj.role).label

    def get_invitation_status(self, obj) -> str:
        # Deliberately three-way rather than reading is_active: a pending
        # invitee and a revoked member are both inactive, but the Teams page
        # offers different actions for each (resend vs reactivate).
        from api.authentication.services.staff_service import StaffService

        if obj.is_active:
            return "ACTIVE"
        if obj.has_usable_password():
            # Accepted the invitation at some point, then had access withdrawn.
            return "REVOKED"
        # Never accepted: still waiting if the invitation is live, otherwise it
        # was withdrawn before they got to it.
        return "PENDING" if StaffService.has_open_invitation(obj) else "REVOKED"

    def get_invited_by(self, obj) -> str | None:
        return obj.created_by.email if obj.created_by else None

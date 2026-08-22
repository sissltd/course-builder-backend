from rest_framework import serializers

from api.collaborators.enums import CollaboratorRole
from api.collaborators.models import CollaboratorInvite, CourseCollaborator
from api.courses.models import Module
from api.courses.serializers.module_serializer import ModuleMiniSerializer
from api.users.models import User


class CollaboratorUserMiniSerializer(serializers.ModelSerializer):
    """Lightweight User representation for a collaborator row/detail panel."""

    class Meta:
        model = User
        fields = ["id", "first_name", "last_name", "email", "country", "sex"]
        read_only_fields = fields


class CollaboratorSerializer(serializers.ModelSerializer):
    """Read-only representation of a CourseCollaborator."""

    user = CollaboratorUserMiniSerializer(read_only=True)
    assigned_modules = ModuleMiniSerializer(many=True, read_only=True)

    class Meta:
        model = CourseCollaborator
        fields = ["id", "user", "role", "assigned_modules", "created_datetime"]
        read_only_fields = fields


class CollaboratorInviteCreateSerializer(serializers.Serializer):
    """Request body for sending a course collaboration invite."""

    course_id = serializers.UUIDField()
    email = serializers.EmailField()
    role = serializers.ChoiceField(
        choices=CollaboratorRole.choices,
        required=False,
        default=CollaboratorRole.COLLABORATOR,
    )
    assigned_modules = serializers.PrimaryKeyRelatedField(
        many=True, queryset=Module.objects.all(), required=False
    )


class CollaboratorInviteSerializer(serializers.ModelSerializer):
    """Representation of a CollaboratorInvite across its lifecycle.

    `invitee` is resolved from the email when an account exists, so the
    manage panel can show a name instead of just an address. The token is
    excluded from list output - it identifies the invite in notification
    deep-links and isn't part of the management surface.
    """

    assigned_modules = ModuleMiniSerializer(many=True, read_only=True)
    invitee = CollaboratorUserMiniSerializer(read_only=True, source="invitee_user")
    is_expired = serializers.SerializerMethodField()

    class Meta:
        model = CollaboratorInvite
        fields = [
            "id",
            "course",
            "email",
            "invitee",
            "role",
            "assigned_modules",
            "status",
            "is_expired",
            "expires_at",
            "responded_at",
            "created_datetime",
        ]
        read_only_fields = fields

    def get_is_expired(self, obj) -> bool:
        from api.collaborators.services import invite_service

        return invite_service.invite_is_expired(obj)


class CollaboratorRoleUpdateSerializer(serializers.Serializer):
    """Request body for changing a collaborator's role and/or module
    assignment. Both fields are optional so a PATCH can update either
    independently - DRF's `partial=True` on this view makes that so even
    though `role` isn't itself declared with `required=False`."""

    role = serializers.ChoiceField(choices=CollaboratorRole.choices, required=False)
    assigned_modules = serializers.PrimaryKeyRelatedField(
        many=True, queryset=Module.objects.all(), required=False
    )

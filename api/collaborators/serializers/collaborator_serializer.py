from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from api.collaborators.enums import CollaboratorRole
from api.collaborators.models import CollaboratorInvite, CourseCollaborator
from api.courses.models import Module
from api.courses.serializers.module_serializer import ModuleMiniSerializer


class CollaboratorSerializer(serializers.ModelSerializer):
    """Collaborator fields named for the Collaborators screen.

    The screen renders a single row from ``name``, ``email``, ``date_added``
    and ``role``.  ``country_of_origin`` is used in its detail drawer.
    """

    name = serializers.SerializerMethodField()
    email = serializers.EmailField(source="user.email", read_only=True)
    country_of_origin = serializers.CharField(source="user.country", read_only=True)
    date_added = serializers.DateTimeField(source="created_datetime", read_only=True)
    role_label = serializers.CharField(source="get_role_display", read_only=True)
    assigned_modules = ModuleMiniSerializer(many=True, read_only=True)

    class Meta:
        model = CourseCollaborator
        fields = [
            "id",
            "name",
            "email",
            "country_of_origin",
            "date_added",
            "role",
            "role_label",
            "assigned_modules",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.CharField())
    def get_name(self, obj) -> str:
        return (
            " ".join(
                part for part in (obj.user.first_name, obj.user.last_name) if part
            ).strip()
            or obj.user.email
        )


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
    invitee = serializers.SerializerMethodField()
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

    @extend_schema_field(serializers.DictField(allow_null=True))
    def get_invitee(self, obj) -> dict | None:
        """Resolve the invite's email to an account when one exists, flat
        style matching CollaboratorSerializer's screen fields."""

        user = obj.invitee_user
        if user is None:
            return None
        name = (
            " ".join(part for part in (user.first_name, user.last_name) if part).strip()
            or user.email
        )
        return {"id": user.id, "name": name, "email": user.email}


class CollaboratorRoleUpdateSerializer(serializers.Serializer):
    """Request body for changing a collaborator's role and/or module
    assignment. Both fields are optional so a PATCH can update either
    independently - DRF's `partial=True` on this view makes that so even
    though `role` isn't itself declared with `required=False`."""

    role = serializers.ChoiceField(choices=CollaboratorRole.choices, required=False)
    assigned_modules = serializers.PrimaryKeyRelatedField(
        many=True, queryset=Module.objects.all(), required=False
    )

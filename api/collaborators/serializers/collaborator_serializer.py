from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from api.collaborators.enums import CollaboratorRole
from api.collaborators.models import CourseCollaborator
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


class CollaboratorInviteSerializer(serializers.Serializer):
    """Request body for inviting a collaborator onto a course."""

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


class CollaboratorRoleUpdateSerializer(serializers.Serializer):
    """Request body for changing a collaborator's role and/or module
    assignment. Both fields are optional so a PATCH can update either
    independently - DRF's `partial=True` on this view makes that so even
    though `role` isn't itself declared with `required=False`."""

    role = serializers.ChoiceField(choices=CollaboratorRole.choices, required=False)
    assigned_modules = serializers.PrimaryKeyRelatedField(
        many=True, queryset=Module.objects.all(), required=False
    )

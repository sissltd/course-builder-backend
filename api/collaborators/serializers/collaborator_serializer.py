from rest_framework import serializers

from api.collaborators.enums import CollaboratorRole
from api.collaborators.models import CourseCollaborator
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

    class Meta:
        model = CourseCollaborator
        fields = ["id", "user", "role", "created_datetime"]
        read_only_fields = fields


class CollaboratorInviteSerializer(serializers.Serializer):
    """Request body for inviting a collaborator onto a course."""

    course_id = serializers.UUIDField()
    email = serializers.EmailField()
    role = serializers.ChoiceField(
        choices=CollaboratorRole.choices,
        required=False,
        default=CollaboratorRole.COLLABORATOR,
    )


class CollaboratorRoleUpdateSerializer(serializers.Serializer):
    """Request body for changing a collaborator's role."""

    role = serializers.ChoiceField(choices=CollaboratorRole.choices)

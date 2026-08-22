from rest_framework import serializers

from api.collaborators.models import WorkspaceCollaborator


class WorkspaceCollaboratorSerializer(serializers.ModelSerializer):
    """Representation of one person on the account-level team roster."""

    class Meta:
        model = WorkspaceCollaborator
        fields = [
            "id",
            "owner",
            "user",
            "invited_email",
            "role",
            "sex",
            "country_of_origin",
            "status",
            "removed_at",
            "created_datetime",
        ]
        read_only_fields = ["id", "owner", "user", "removed_at", "created_datetime"]

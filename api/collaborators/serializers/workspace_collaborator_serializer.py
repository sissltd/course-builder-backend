from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from api.collaborators.models import WorkspaceCollaborator


class WorkspaceCollaboratorSerializer(serializers.ModelSerializer):
    """Representation of one person on the account-level team roster."""

    name = serializers.SerializerMethodField()
    email = serializers.EmailField(source="invited_email", read_only=True)
    date_added = serializers.DateTimeField(source="created_datetime", read_only=True)
    role_label = serializers.CharField(source="get_role_display", read_only=True)

    class Meta:
        model = WorkspaceCollaborator
        fields = [
            "id",
            "name",
            "email",
            "owner",
            "user",
            "invited_email",
            "role",
            "role_label",
            "sex",
            "country_of_origin",
            "status",
            "removed_at",
            "date_added",
            "created_datetime",
        ]
        read_only_fields = ["id", "owner", "user", "removed_at", "created_datetime"]

    @extend_schema_field(serializers.CharField())
    def get_name(self, obj) -> str:
        if obj.user_id:
            full_name = " ".join(
                part for part in (obj.user.first_name, obj.user.last_name) if part
            ).strip()
            if full_name:
                return full_name
        return obj.invited_email

    def to_representation(self, instance):
        """Prefer linked profile demographics while retaining invite fallbacks."""

        data = super().to_representation(instance)
        if instance.user_id:
            data["sex"] = instance.user.sex or data["sex"]
            data["country_of_origin"] = (
                instance.user.country or data["country_of_origin"]
            )
        return data

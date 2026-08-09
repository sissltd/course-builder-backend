from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from api.authentication.models import UserSession


class UserSessionSerializer(serializers.ModelSerializer):
    """Read-only representation of one of the current user's active sessions."""

    is_current = serializers.SerializerMethodField()

    class Meta:
        model = UserSession
        fields = [
            "id",
            "ip_address",
            "user_agent",
            "created_datetime",
            "last_seen_at",
            "is_current",
        ]
        read_only_fields = fields

    @extend_schema_field(OpenApiTypes.BOOL)
    def get_is_current(self, obj) -> bool:
        request = self.context.get("request")
        auth = getattr(request, "auth", None) if request is not None else None
        if auth is None:
            return False
        return auth.get("sid") == str(obj.id)

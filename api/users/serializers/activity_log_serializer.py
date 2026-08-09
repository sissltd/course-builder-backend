from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from api.users.models import UserActivityLog


class UserActivityLogSerializer(serializers.ModelSerializer):
    """Read-only representation of a UserActivityLog entry."""

    actor = serializers.SerializerMethodField()

    class Meta:
        model = UserActivityLog
        fields = [
            "id",
            "category",
            "action",
            "summary",
            "details",
            "actor",
            "activity_datetime",
        ]
        read_only_fields = fields

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_actor(self, obj):
        if not obj.actor_user_id:
            return None
        return {
            "id": obj.actor_user_id,
            "first_name": obj.actor_user.first_name,
            "last_name": obj.actor_user.last_name,
            "email": obj.actor_user.email,
        }

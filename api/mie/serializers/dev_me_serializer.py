from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from api.mie.models import DeveloperAccount
from api.mie.serializers.webhook_endpoint_serializer import WebhookEndpointSerializer
from api.mie.services import webhook_endpoint_service


class DeveloperMeSerializer(serializers.ModelSerializer):
    """The authenticated developer's own account snapshot.

    The API key is masked - the full key was shown exactly once at
    issuance and lives nowhere in our database. The signing secret IS
    included: it only ever verifies our messages, never authenticates
    theirs, so it is safe to re-display (rotate it if it leaks).
    """

    api_key_preview = serializers.SerializerMethodField(
        help_text="Masked prefix of the current API key, or null before issuance."
    )
    email = serializers.EmailField(
        help_text="Registration identity; also the platform OTP login handle."
    )
    webhook_endpoints = serializers.SerializerMethodField(
        help_text=(
            "Every endpoint the account receives webhooks on, with the events "
            "each takes. Manage them at /mie/v1/webhooks/."
        )
    )

    class Meta:
        model = DeveloperAccount
        fields = (
            "email",
            "status",
            "plan_type",
            "webhook_endpoints",
            "api_key_preview",
            "api_key_last_used_at",
            "signing_secret",
            "created_datetime",
            "decided_at",
        )
        read_only_fields = fields

    @extend_schema_field(WebhookEndpointSerializer(many=True))
    def get_webhook_endpoints(self, obj) -> list[dict]:
        return WebhookEndpointSerializer(
            webhook_endpoint_service.live_endpoints(developer=obj), many=True
        ).data

    def get_api_key_preview(self, obj) -> str | None:
        if not obj.api_key_prefix:
            return None
        return f"{obj.api_key_prefix}..."

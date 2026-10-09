from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from api.mie.enums import MiePlanType, MieSourceType
from api.mie.models import DeveloperAccount
from api.mie.serializers.webhook_endpoint_serializer import WebhookEndpointSerializer
from api.mie.services import webhook_endpoint_service


class DeveloperRegisterSerializer(serializers.Serializer):
    """Payload for registering an external developer (starts PENDING)."""

    email = serializers.EmailField(
        help_text=(
            "The developer's identity. Used for API-key issuance and as the "
            "login handle for platform (OTP) access to their surfaces."
        )
    )
    webhook_url = serializers.URLField(
        help_text=(
            "HTTPS endpoint that will receive signed POST notifications. It "
            "becomes the account's first webhook endpoint, taking every "
            "event; the developer can add more and choose their events once "
            "approved."
        )
    )
    plan_type = serializers.ChoiceField(
        choices=MiePlanType.choices,
        default=MiePlanType.PAID_PER_SUBMISSION,
        help_text=(
            "Payout arrangement: PAID_PER_SUBMISSION pays the developer for "
            "each course published from one of their ideas (approval alone "
            "pays nothing); BYPASS_PER_SUBMISSION allows per-idea "
            "no-payout marks; BYPASS_ACCOUNT never pays this developer."
        ),
    )


class DeveloperAccountAdminSerializer(serializers.ModelSerializer):
    """Developer account as seen by superadmins. Never contains key
    material - only the non-secret display prefix.

    `webhook_endpoints` reads the `live_webhook_endpoints` prefetch when the
    queryset declares it (see MieDeveloperAdminViewSet), so a list costs the
    same however many accounts it shows.
    """

    webhook_endpoints = serializers.SerializerMethodField(
        help_text="Every live endpoint the developer receives webhooks on, with its events."
    )
    api_key_preview = serializers.SerializerMethodField(
        help_text="Masked prefix of the current API key, or null before issuance."
    )
    # Declared rather than inferred: the model field is editable=False, so a
    # ModelSerializer would fall back to a bare read-only string and the
    # documented value set would disappear from the schema.
    source_type = serializers.ChoiceField(
        choices=MieSourceType.choices,
        read_only=True,
        help_text=(
            "EXTERNAL for a third-party developer, SYSTEM for a platform-"
            "owned integration (the MIE crawler). Set only by the "
            "provisioning command; no registration path can change it."
        ),
    )

    class Meta:
        model = DeveloperAccount
        fields = (
            "id",
            "email",
            "webhook_endpoints",
            "status",
            "plan_type",
            "source_type",
            "api_key_preview",
            "api_key_issued_at",
            "api_key_last_used_at",
            "decided_at",
            "created_datetime",
            "updated_datetime",
        )
        read_only_fields = fields

    @extend_schema_field(WebhookEndpointSerializer(many=True))
    def get_webhook_endpoints(self, obj) -> list[dict]:
        endpoints = getattr(obj, "live_webhook_endpoints", None)
        if endpoints is None:
            endpoints = webhook_endpoint_service.live_endpoints(developer=obj)
        return WebhookEndpointSerializer(endpoints, many=True).data

    def get_api_key_preview(self, obj) -> str | None:
        if not obj.api_key_prefix:
            return None
        return f"{obj.api_key_prefix}..."


class DeveloperApprovalResponseSerializer(serializers.Serializer):
    """Result of approving a developer account.

    one_time_api_key is populated ONLY when credentials were freshly
    issued - it is shown once here and can never be retrieved again.
    When null, the account reuses its existing key.
    """

    account = DeveloperAccountAdminSerializer(
        help_text="The approved account in admin representation."
    )
    one_time_api_key = serializers.CharField(
        allow_null=True,
        help_text=(
            "Full scb_live_... key. Shown exactly once at issuance; only "
            "its SHA-256 hash is stored. Null when existing credentials "
            "remain valid."
        ),
    )


class DeveloperActionResponseSerializer(serializers.Serializer):
    """Simple acknowledgement envelope for state-changing admin actions."""

    detail = serializers.CharField(
        help_text="Human-readable confirmation of what changed."
    )

from rest_framework import serializers

from api.mie.enums import MiePlanType
from api.mie.models import DeveloperAccount


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
            "HTTPS endpoint that will receive signed POST notifications for "
            "every event against this developer's submissions."
        )
    )
    plan_type = serializers.ChoiceField(
        choices=MiePlanType.choices,
        default=MiePlanType.PAID_PER_SUBMISSION,
        help_text=(
            "Payout arrangement: PAID_PER_SUBMISSION credits the creator "
            "wallet per approval; BYPASS_PER_SUBMISSION allows per-idea "
            "no-payout marks; BYPASS_ACCOUNT never pays this developer."
        ),
    )


class DeveloperAccountAdminSerializer(serializers.ModelSerializer):
    """Developer account as seen by superadmins. Never contains key
    material - only the non-secret display prefix."""

    api_key_preview = serializers.SerializerMethodField(
        help_text="Masked prefix of the current API key, or null before issuance."
    )

    class Meta:
        model = DeveloperAccount
        fields = (
            "id",
            "email",
            "webhook_url",
            "status",
            "plan_type",
            "api_key_preview",
            "api_key_issued_at",
            "api_key_last_used_at",
            "decided_at",
            "created_datetime",
            "updated_datetime",
        )
        read_only_fields = fields

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

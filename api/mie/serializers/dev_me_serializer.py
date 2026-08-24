from rest_framework import serializers

from api.mie.models import DeveloperAccount


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

    class Meta:
        model = DeveloperAccount
        fields = (
            "email",
            "status",
            "plan_type",
            "webhook_url",
            "api_key_preview",
            "api_key_last_used_at",
            "signing_secret",
            "created_datetime",
            "decided_at",
        )
        read_only_fields = fields

    def get_api_key_preview(self, obj) -> str | None:
        if not obj.api_key_prefix:
            return None
        return f"{obj.api_key_prefix}..."

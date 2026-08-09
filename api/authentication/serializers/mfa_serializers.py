from rest_framework import serializers


class MFAEnrollResponseSerializer(serializers.Serializer):
    """Output of POST /auth/mfa/enroll/ - not used to validate input."""

    secret = serializers.CharField()
    otpauth_uri = serializers.CharField()
    qr_code_base64 = serializers.CharField()


class MFACodeSerializer(serializers.Serializer):
    """Shared input shape for every endpoint that just needs a live TOTP (or,
    where noted, recovery) code: enroll-confirm, recovery-regenerate,
    disable."""

    code = serializers.CharField(max_length=32)


class MFAVerifySerializer(serializers.Serializer):
    """Input for POST /auth/mfa/verify/ - the second half of login."""

    challenge_token = serializers.CharField()
    code = serializers.CharField(max_length=32)


class MFARecoveryCodesResponseSerializer(serializers.Serializer):
    """Output shape for any endpoint returning a freshly generated batch of
    recovery codes - shown once, never retrievable again afterward."""

    recovery_codes = serializers.ListField(child=serializers.CharField())

from rest_framework import serializers


class ChangeEmailRequestSerializer(serializers.Serializer):
    """Request body for POST /auth/change-email/ - proves identity via
    password, then a confirmation link is emailed to new_email."""

    new_email = serializers.EmailField()
    password = serializers.CharField(write_only=True)


class ChangeEmailConfirmSerializer(serializers.Serializer):
    """Request body for POST /auth/change-email/confirm/. Only the token is
    needed - the confirming link is opened from the new inbox, where the
    caller may not be authenticated at all."""

    token = serializers.CharField()

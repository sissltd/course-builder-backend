from rest_framework import serializers


class LogoutSerializer(serializers.Serializer):
    """Field name matches simplejwt's own TokenRefreshSerializer ("refresh")."""

    refresh = serializers.CharField()

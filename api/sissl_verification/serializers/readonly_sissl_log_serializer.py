"""
Read-only serializer for SISSLLog rows — used by the admin log views.

PII has already been stripped at the write site (the service); this
serializer simply exposes the fields as they sit in the DB.
"""

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from api.sissl_verification.models import SISSLLog


# >>>>>>>>>>>>>>>>>>>>>>>>> Log Serializer <<<<<<<<<<<<<<<<<<<<<<<<<<<
class SISSLLogSerializer(serializers.ModelSerializer):
    """
    Full SISSLLog row for the admin detail view.

    `user_email` is denormalized so an admin doesn't need a second round-trip
    to map user UUIDs to readable identities.
    """

    user_email = serializers.SerializerMethodField()

    class Meta:
        model = SISSLLog
        fields = [
            "id",
            "user",
            "user_email",
            "kind",
            "status",
            "request_summary",
            "response_summary",
            "latency_ms",
            "error_message",
            "cost",
            "created_datetime",
        ]
        read_only_fields = fields  # this view never writes

    @extend_schema_field(serializers.EmailField(allow_null=True))
    def get_user_email(self, obj):
        return obj.user.email if obj.user else None


# >>>>>>>>>>>>>>>>>>>>>>>>> Log List Serializer <<<<<<<<<<<<<<<<<<<<<<
class SISSLLogListSerializer(serializers.ModelSerializer):
    """
    Lightweight version for the admin list view — drops the redacted
    JSON summary blobs which can otherwise bloat list responses.
    """

    user_email = serializers.SerializerMethodField()

    class Meta:
        model = SISSLLog
        fields = [
            "id",
            "user",
            "user_email",
            "kind",
            "status",
            "latency_ms",
            "error_message",
            "cost",
            "created_datetime",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.EmailField(allow_null=True))
    def get_user_email(self, obj):
        return obj.user.email if obj.user else None

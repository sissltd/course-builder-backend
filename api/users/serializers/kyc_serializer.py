from rest_framework import serializers

from api.users.enums import KYCDocumentType
from api.users.models import KYCVerification


class KYCVerificationSerializer(serializers.ModelSerializer):
    """Read-only representation of a KYC submission."""

    class Meta:
        model = KYCVerification
        fields = [
            "id",
            "country_of_issue",
            "document_type",
            "status",
            "rejection_reason",
            "created_datetime",
            "reviewed_at",
        ]
        read_only_fields = fields


class KYCVerificationSubmitSerializer(serializers.Serializer):
    """Write serializer for POST /users/me/kyc/.

    Mirrors the "Document type" -> "Enter ID number" design flow: country of
    issue, one of the four supported document types, and the raw ID number.
    """

    country_of_issue = serializers.CharField(max_length=2)
    document_type = serializers.ChoiceField(choices=KYCDocumentType.choices)
    id_number = serializers.CharField(max_length=64)

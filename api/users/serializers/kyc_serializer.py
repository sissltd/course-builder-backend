from typing import ClassVar

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from api.users.enums import KYCDocumentType
from api.users.models import KYCVerification, User


class KYCUserDataSerializer(serializers.Serializer):
    first_name = serializers.CharField(allow_null=True, read_only=True)
    last_name = serializers.CharField(allow_null=True, read_only=True)
    date_of_birth = serializers.DateField(allow_null=True, read_only=True)
    sex = serializers.CharField(allow_null=True, read_only=True)


class KYCVerificationSerializer(serializers.ModelSerializer):
    """Read-only representation of a KYC submission (the submitter's own view)."""

    first_name = serializers.CharField(source="user.first_name", read_only=True)
    last_name = serializers.CharField(source="user.last_name", read_only=True)
    kyc_user_data = serializers.SerializerMethodField(read_only=True)

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
            "first_name",
            "last_name",
            "kyc_user_data",
            "kyc_request_status",
            "kyc_response_summary",
        ]
        read_only_fields = fields

    @extend_schema_field(KYCUserDataSerializer)
    def get_kyc_user_data(self, obj):
        """Return the user data returned from the KYC provider, if any."""
        return {
            "first_name": obj.user.kyc_first_name,
            "last_name": obj.user.kyc_last_name,
            "date_of_birth": obj.user.kyc_date_of_birth,
            "sex": obj.user.kyc_gender,
        }


class KYCVerificationSubmitSerializer(serializers.Serializer):
    """Write serializer for POST /users/me/kyc/.

    Mirrors the "Document type" -> "Enter ID number" design flow: country of
    issue, one of the four supported document types, and the raw ID number.
    """

    first_name = serializers.CharField(max_length=64)
    last_name = serializers.CharField(max_length=64)
    country_of_issue = serializers.CharField(max_length=2)
    document_type = serializers.ChoiceField(choices=KYCDocumentType.choices)
    id_number = serializers.CharField(max_length=64)


class UserMiniSerializer(serializers.ModelSerializer):
    """Lightweight User representation for nesting inside review-queue payloads."""

    class Meta:
        model = User
        fields = ["id", "email", "first_name", "last_name"]
        read_only_fields = fields


class KYCVerificationAdminSerializer(serializers.ModelSerializer):
    """Read-only representation of a KYC submission for the admin review
    queue - includes the submitting user and the raw id_number an Admin/
    Super Admin needs to actually verify the document, unlike the
    submitter-facing KYCVerificationSerializer."""

    user = UserMiniSerializer(read_only=True)
    reviewed_by = UserMiniSerializer(read_only=True)
    kyc_user_data = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = KYCVerification
        fields: ClassVar = [
            "id",
            "user",
            "country_of_issue",
            "document_type",
            "id_number",
            "status",
            "rejection_reason",
            "reviewed_by",
            "reviewed_at",
            "created_datetime",
            "kyc_user_data",
            "kyc_request_status",
            "kyc_response_summary",
        ]
        read_only_fields = fields

    @extend_schema_field(KYCUserDataSerializer)
    def get_kyc_user_data(self, obj):
        """Return the user data returned from the KYC provider, if any."""
        return {
            "first_name": obj.user.kyc_first_name,
            "last_name": obj.user.kyc_last_name,
            "date_of_birth": obj.user.kyc_date_of_birth,
            "sex": obj.user.kyc_gender,
        }


class KYCReviewApproveSerializer(serializers.Serializer):
    """Request body for the KYC review-queue approve action. No fields
    required - approval needs no accompanying data."""


class KYCReviewRejectSerializer(serializers.Serializer):
    """Request body for the KYC review-queue reject action.

    Requires a non-empty rejection_reason the submitting user can act on
    when resubmitting.
    """

    rejection_reason = serializers.CharField()

    def validate_rejection_reason(self, value: str) -> str:
        if not value.strip():
            raise serializers.ValidationError("rejection_reason must not be empty.")
        return value

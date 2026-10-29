from rest_framework import serializers

from api.mie.models import SubmissionRejectionReason


class RejectionReasonSerializer(serializers.ModelSerializer):
    """A taxonomy label admins attach to rejections."""

    class Meta:
        model = SubmissionRejectionReason
        fields = ("id", "label", "description", "is_active", "created_datetime")
        read_only_fields = ("id", "created_datetime")

    def validate_label(self, value: str) -> str:
        return value.strip()

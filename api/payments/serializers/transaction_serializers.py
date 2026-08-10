from typing import ClassVar

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from api.payments.models.transaction_model import Transaction


class CourseMiniSerializer(serializers.Serializer):
    """Lightweight Course representation for nesting inside Transaction payloads."""

    id = serializers.UUIDField()
    title = serializers.CharField()


class TransactionSerializer(serializers.ModelSerializer):
    """Read-only representation of a wallet Transaction."""

    course = serializers.SerializerMethodField()

    class Meta:
        model = Transaction
        fields: ClassVar[list[str]] = [
            "id",
            "reference",
            "course",
            "amount",
            "fee",
            "type",
            "status",
            "description",
            "recipient_account_name",
            "recipient_account_number",
            "recipient_provider_name",
            "created_datetime",
        ]
        read_only_fields = fields

    @extend_schema_field(CourseMiniSerializer(allow_null=True))
    def get_course(self, obj):
        if not obj.course_id:
            return None
        return CourseMiniSerializer(
            {"id": obj.course_id, "title": obj.course.title}
        ).data


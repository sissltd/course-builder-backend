from decimal import Decimal

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from api.wallet.models import Transaction, Wallet
from api.wallet.services import wallet_service


class WalletSerializer(serializers.ModelSerializer):
    """Read-only representation of the current user's wallet."""

    class Meta:
        model = Wallet
        fields = ["id", "balance", "currency", "updated_datetime"]
        read_only_fields = fields


class CourseMiniSerializer(serializers.Serializer):
    """Lightweight Course representation for nesting inside Transaction payloads."""

    id = serializers.UUIDField()
    title = serializers.CharField()


class TransactionSerializer(serializers.ModelSerializer):
    """Read-only representation of a wallet Transaction."""

    course = serializers.SerializerMethodField()

    class Meta:
        model = Transaction
        fields = ["id", "course", "amount", "type", "status", "description", "created_datetime"]
        read_only_fields = fields

    @extend_schema_field(CourseMiniSerializer(allow_null=True))
    def get_course(self, obj):
        if not obj.course_id:
            return None
        return CourseMiniSerializer({"id": obj.course_id, "title": obj.course.title}).data


class WithdrawalRequestSerializer(serializers.Serializer):
    """Request body for creating a withdrawal request.

    Delegates persistence and business validation (minimum threshold,
    sufficient balance) to wallet_service.create_withdrawal_request.
    """

    amount = serializers.DecimalField(max_digits=12, decimal_places=2)

    def validate_amount(self, value: Decimal) -> Decimal:
        if value <= 0:
            raise serializers.ValidationError("amount must be greater than 0.")
        return value

    def create(self, validated_data):
        request = self.context["request"]
        return wallet_service.create_withdrawal_request(
            user=request.user, amount=validated_data["amount"]
        )

    def to_representation(self, instance):
        return TransactionSerializer(instance, context=self.context).data

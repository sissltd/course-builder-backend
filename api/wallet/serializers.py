from decimal import Decimal

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from api.payments.models.transaction_model import Transaction
from api.wallet.enums import PayoutAccountType
from api.wallet.models import PayoutAccount, Wallet, WithdrawalRequest
from api.wallet.services import wallet_service
from shared.utils.encryption import decrypt_field


class WalletSerializer(serializers.ModelSerializer):
    """Read-only representation of the current user's wallet, including the
    total-earned/pending-payments figures the dashboard shows alongside
    balance (see wallet_service.get_wallet_totals)."""

    total_earned = serializers.SerializerMethodField()
    pending_balance = serializers.SerializerMethodField()

    class Meta:
        model = Wallet
        fields = [
            "id",
            "balance",
            "currency",
            "total_earned",
            "pending_balance",
            "updated_datetime",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.DecimalField(max_digits=12, decimal_places=2))
    def get_total_earned(self, obj) -> Decimal:
        return wallet_service.get_wallet_totals(wallet=obj)["total_earned"]

    @extend_schema_field(serializers.DecimalField(max_digits=12, decimal_places=2))
    def get_pending_balance(self, obj) -> Decimal:
        return wallet_service.get_wallet_totals(wallet=obj)["pending_balance"]


class CourseMiniSerializer(serializers.Serializer):
    """Lightweight Course representation for nesting inside Transaction payloads."""

    id = serializers.UUIDField()
    title = serializers.CharField()


class TransactionSerializer(serializers.ModelSerializer):
    """Read-only representation of a wallet Transaction."""

    course = serializers.SerializerMethodField()

    class Meta:
        model = Transaction
        fields = [
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
    
    def to_representation(self, instance):
        representation = super().to_representation(instance)
        if instance.recipient_account_number:
            representation['recipient_account_number'] = decrypt_field(instance.recipient_account_number)

        return representation



class PayoutAccountSerializer(serializers.ModelSerializer):
    """Read-only representation of a payout account."""

    class Meta:
        model = PayoutAccount
        fields = [
            "id",
            "account_type",
            "provider_name",
            "account_number",
            "account_name",
            "is_default",
            "created_datetime",
        ]
        read_only_fields = fields


class PayoutAccountCreateSerializer(serializers.Serializer):
    """Request body for POST /wallet/payout-accounts/ (the "Add local/mobile
    account" forms)."""

    account_type = serializers.ChoiceField(choices=PayoutAccountType.choices)
    provider_name = serializers.CharField(max_length=100)
    account_number = serializers.CharField(max_length=34)
    account_name = serializers.CharField(max_length=150)
    is_default = serializers.BooleanField(required=False, default=False)

    def create(self, validated_data):
        return wallet_service.create_payout_account(
            user=self.context["request"].user, **validated_data
        )

    def to_representation(self, instance):
        return PayoutAccountSerializer(instance, context=self.context).data


class WithdrawalRequestSerializer(serializers.ModelSerializer):
    """Read-only representation of a WithdrawalRequest."""

    class Meta:
        model = WithdrawalRequest
        fields = ["id", "amount", "payout_account", "status", "created_datetime"]
        read_only_fields = fields


class WithdrawalRequestCreateSerializer(serializers.Serializer):
    """Request body for POST /wallet/withdrawals/ - step 1 of the withdrawal
    flow ("Enter amount" + "Select available account"). Creates a
    WithdrawalRequest and emails an OTP; does not move any money yet.
    """

    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    payout_account = serializers.UUIDField()

    def validate_amount(self, value: Decimal) -> Decimal:
        if value <= 0:
            raise serializers.ValidationError("amount must be greater than 0.")
        return value

    def create(self, validated_data):
        request = self.context["request"]
        return wallet_service.request_withdrawal(
            user=request.user,
            amount=validated_data["amount"],
            payout_account_id=validated_data["payout_account"],
        )

    def to_representation(self, instance):
        return WithdrawalRequestSerializer(instance, context=self.context).data


class WithdrawalConfirmSerializer(serializers.Serializer):
    """Request body for POST /wallet/withdrawals/<id>/confirm/ - step 2 of
    the withdrawal flow ("Confirm withdrawal" OTP entry). Creates the
    resulting Transaction once the code is verified.
    """

    code = serializers.CharField(max_length=10)

    def create(self, validated_data):
        request = self.context["request"]
        return wallet_service.confirm_withdrawal(
            user=request.user,
            withdrawal_request_id=self.context["withdrawal_request_id"],
            code=validated_data["code"],
        )

    def to_representation(self, instance):
        return TransactionSerializer(instance, context=self.context).data

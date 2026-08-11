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


class WalletOwnerMiniSerializer(serializers.Serializer):
    """Lightweight User representation for nesting inside admin wallet payloads.

    An admin reading a wallet row needs to know whose it is; the creator-facing
    serializers omit this because the owner is always the caller.
    """

    id = serializers.UUIDField()
    email = serializers.EmailField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()


class AdminWalletSerializer(serializers.ModelSerializer):
    """Read-only representation of any creator's wallet, for the admin finance
    view. WalletSerializer's totals are deliberately not repeated here - they
    cost an aggregate query per row, which is affordable on the single-wallet
    creator view but not down a paginated admin list."""

    user = WalletOwnerMiniSerializer(read_only=True)

    class Meta:
        model = Wallet
        fields = ["id", "user", "balance", "currency", "updated_datetime"]
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
            representation["recipient_account_number"] = decrypt_field(
                instance.recipient_account_number
            )

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


class AdminTransactionSerializer(TransactionSerializer):
    """TransactionSerializer plus the owning creator, for the admin ledger.

    Subclasses rather than duplicates: an admin should see exactly what the
    creator sees on their own statement, with the owner added - so a field
    added to the creator view appears here automatically.
    """

    user = WalletOwnerMiniSerializer(source="wallet.user", read_only=True)

    class Meta(TransactionSerializer.Meta):
        fields = ["user"] + TransactionSerializer.Meta.fields
        read_only_fields = fields


class AdminWithdrawalRequestSerializer(serializers.ModelSerializer):
    """Read-only representation of a withdrawal request for the admin payout
    worklist, including the destination account and - once confirmed - the
    reference of the transaction it produced."""

    user = WalletOwnerMiniSerializer(read_only=True)
    payout_account = PayoutAccountSerializer(read_only=True)
    transaction_reference = serializers.CharField(
        source="transaction.reference", read_only=True, default=None
    )

    class Meta:
        model = WithdrawalRequest
        fields = [
            "id",
            "user",
            "amount",
            "status",
            "payout_account",
            "transaction_reference",
            "confirmed_at",
            "created_datetime",
        ]
        read_only_fields = fields

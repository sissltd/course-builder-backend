from typing import ClassVar

from rest_framework import serializers

from api.wallets.models.bankaccount_models import BankAccount
from shared.utils.encryption import decrypt_field


class BankAccountCreateSerializer(serializers.ModelSerializer):

    class Meta:
        model = BankAccount
        fields: ClassVar[list[str]] = [
            "bank_name",
            "account_name",
            "account_number",
            "bank_code",
            "is_default",
        ]
        read_only_fields: ClassVar[list[str]] = ["bank_name"]

    def validate_account_number(self, value):
        if not value.isdigit():
            raise serializers.ValidationError("Account number must contain only digits.")
        return value


class BankAccountListSerializer(serializers.ModelSerializer):
    account_number = serializers.SerializerMethodField()

    def get_account_number(self, obj) -> str:
        return decrypt_field(obj.account_number)

    class Meta:
        model = BankAccount
        fields: ClassVar[list[str]] = [
            "id",
            "bank_name",
            "account_name",
            "account_number",
            "bank_code",
            "is_default",
        ]


class BankAccountVerifySerializer(serializers.Serializer):
    bank_code = serializers.CharField()
    account_number = serializers.CharField()

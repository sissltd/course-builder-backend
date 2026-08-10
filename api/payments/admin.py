from django.contrib import admin

from api.payments.models.bankaccount_models import BankAccount
from api.payments.models.transaction_model import Transaction


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "wallet",
        "reference",
        "amount",
        "type",
        "status",
        "created_datetime",
    )
    list_filter = ("type", "status")
    search_fields = ("reference",)


@admin.register(BankAccount)
class BankAccountAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "account_type", "bank_name", "is_default")
    list_filter = ("account_type",)
    search_fields = ("user__email", "account_number")

from django.contrib import admin

from api.wallet.models import PayoutAccount, Transaction, Wallet, WithdrawalRequest


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "balance", "currency", "updated_datetime")
    search_fields = ("user__email",)


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


@admin.register(PayoutAccount)
class PayoutAccountAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "account_type", "provider_name", "is_default")
    list_filter = ("account_type",)
    search_fields = ("user__email", "account_number")


@admin.register(WithdrawalRequest)
class WithdrawalRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "amount", "status", "created_datetime")
    list_filter = ("status",)

from django.contrib import admin

from api.wallet.models import Transaction, Wallet


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "balance", "currency", "updated_datetime")
    search_fields = ("user__email",)


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ("id", "wallet", "amount", "type", "status", "created_datetime")
    list_filter = ("type", "status")

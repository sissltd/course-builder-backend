from django.contrib import admin

from api.wallet.models import Wallet, WithdrawalRequest


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "balance", "currency", "updated_datetime")
    search_fields = ("user__email",)

@admin.register(WithdrawalRequest)
class WithdrawalRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "amount", "status", "created_datetime")
    list_filter = ("status",)

from django.contrib import admin

from api.wallet.models import Wallet, WithdrawalRequest


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    """Creator wallets, read-only.

    `balance` is deliberately not editable here. It moves only through
    wallet_service.credit_wallet (course approval) and confirm_withdrawal,
    both of which take a row lock and write a Transaction; typing a new number
    into this form would desynchronize the balance from the transaction log
    that is supposed to explain it, with nothing recording who did it or why.
    """

    list_display = ("id", "user", "balance", "currency", "updated_datetime")
    search_fields = ("user__email",)
    readonly_fields = (
        "id",
        "user",
        "balance",
        "currency",
        "created_datetime",
        "updated_datetime",
    )

    def has_add_permission(self, request):
        return False


@admin.register(WithdrawalRequest)
class WithdrawalRequestAdmin(admin.ModelAdmin):
    """Withdrawal requests, read-only.

    Note there is still no settlement action here or anywhere else: a
    confirmed withdrawal debits the wallet and leaves a PENDING transaction
    that nothing advances to COMPLETED or FAILED. Adding a status dropdown
    would let an operator mark a payout "done" without any money moving, which
    is worse than the current gap being visible.
    """

    list_display = ("id", "user", "amount", "status", "created_datetime")
    list_filter = ("status",)
    search_fields = ("user__email",)
    readonly_fields = (
        "id",
        "user",
        "wallet",
        "payout_account",
        "amount",
        "status",
        "transaction",
        "confirmed_at",
        "created_datetime",
        "updated_datetime",
    )

    def has_add_permission(self, request):
        return False

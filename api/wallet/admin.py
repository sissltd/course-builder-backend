from django.contrib import admin

from api.wallet.models import PayoutAccount, Transaction, Wallet, WithdrawalRequest


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


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    """Wallet transactions, fully read-only.

    The model documents these as an immutable record of a balance change;
    this makes that true in the one place it was previously editable.
    """

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
    search_fields = ("reference", "wallet__user__email")

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PayoutAccount)
class PayoutAccountAdmin(admin.ModelAdmin):
    """Creator payout accounts, read-only - these are the creator's own bank
    details and are never edited on their behalf."""

    list_display = ("id", "user", "account_type", "provider_name", "is_default")
    list_filter = ("account_type",)
    search_fields = ("user__email", "account_number")
    readonly_fields = (
        "id",
        "user",
        "account_type",
        "provider_name",
        "account_number",
        "account_name",
        "is_default",
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

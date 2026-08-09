import django_filters

from api.wallet.models import Transaction, WithdrawalRequest


class TransactionFilter(django_filters.FilterSet):
    class Meta:
        model = Transaction
        fields = {
            "type": ["exact"],
            "status": ["exact"],
        }


class AdminTransactionFilter(django_filters.FilterSet):
    """Filters for the platform-wide transaction ledger.

    Adds ?user= on top of TransactionFilter's type/status, so an admin can
    narrow the whole platform's ledger down to one creator - the question the
    admin view exists to answer.
    """

    user = django_filters.UUIDFilter(
        field_name="wallet__user_id", label="Wallet owner id"
    )

    class Meta:
        model = Transaction
        fields = {
            "type": ["exact"],
            "status": ["exact"],
        }


class AdminWithdrawalRequestFilter(django_filters.FilterSet):
    class Meta:
        model = WithdrawalRequest
        fields = {
            "status": ["exact"],
            "user": ["exact"],
        }

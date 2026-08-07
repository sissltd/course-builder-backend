
from api.payments.models.transaction_model import Transaction
from api.users.models.user import User
from api.wallet.models import WithdrawalRequest


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

    user = django_filters.UUIDFilter(method="filter_user", label="Wallet owner id")

    class Meta:
        model = Transaction
        fields = {
            "type": ["exact"],
            "status": ["exact"],
        }

    def filter_user(self, queryset, name, value):
        user = User.objects.filter(id=value).first()
        if user and user.wallet:
            return queryset.filter(wallet_id=user.wallet.id)
        return queryset


class AdminWithdrawalRequestFilter(django_filters.FilterSet):
    class Meta:
        model = WithdrawalRequest
        fields = {
            "status": ["exact"],
            "user": ["exact"],
        }


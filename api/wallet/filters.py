import django_filters

from api.wallet.models import Transaction


class TransactionFilter(django_filters.FilterSet):
    class Meta:
        model = Transaction
        fields = {
            "type": ["exact"],
            "status": ["exact"],
        }

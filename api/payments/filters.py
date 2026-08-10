from typing import ClassVar

import django_filters

from api.payments.models.transaction_model import Transaction


class TransactionFilter(django_filters.FilterSet):
    class Meta:
        model = Transaction
        fields: ClassVar[dict[str, list[str]]] = {
            "type": ["exact"],
            "status": ["exact"],
        }
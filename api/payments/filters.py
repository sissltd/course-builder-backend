from typing import ClassVar

from django_filters import rest_framework as filters

from api.payments.models.transaction_model import Transaction


class TransactionFilter(filters.FilterSet):
    start_date = filters.DateFilter(
        field_name="created_datetime",
        lookup_expr="date__gte",
        input_formats=["%Y-%m-%d"],
    )
    end_date = filters.DateFilter(
        field_name="created_datetime",
        lookup_expr="date__lte",
        input_formats=["%Y-%m-%d"],
    )

    class Meta:
        model = Transaction
        fields: ClassVar[dict[str, list[str]]] = {
            "type": ["exact"],
            "status": ["exact"],
        }

from typing import ClassVar

from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework import filters as drf_filters
from rest_framework.filters import SearchFilter
from rest_framework.generics import ListAPIView

from api.payments.docs.transaction_docs import TRANSACTION_LIST_DOCS
from api.payments.filters import TransactionFilter
from api.payments.models.transaction_model import Transaction
from api.payments.serializers.transaction_serializers import TransactionSerializer
from api.payments.services.transaction_services import list_transactions
from api.users.permissions import IsCourseCreatorRole


@extend_schema(**TRANSACTION_LIST_DOCS)
class TransactionListView(ListAPIView):
    """The current user's wallet transaction history."""

    permission_classes: ClassVar = [IsCourseCreatorRole]
    serializer_class = TransactionSerializer
    filterset_class = TransactionFilter
    filter_backends: ClassVar = [
        DjangoFilterBackend,
        drf_filters.OrderingFilter,
        SearchFilter,
    ]
    ordering_fields: ClassVar = ["created_datetime", "amount"]
    search_fields: ClassVar = ["reference"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Transaction.objects.none()
        return list_transactions(user=self.request.user)

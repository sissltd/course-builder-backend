from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters as drf_filters
from rest_framework.generics import ListAPIView

from api.payments.filters import TransactionFilter
from api.payments.models.transaction_model import Transaction
from api.payments.serializers.transaction_serializers import TransactionSerializer
from api.payments.services.transaction_services import list_transactions
from api.users.permissions import IsCourseCreatorRole


class TransactionListView(ListAPIView):
    """The current user's wallet transaction history."""

    permission_classes = [IsCourseCreatorRole]
    serializer_class = TransactionSerializer
    filterset_class = TransactionFilter
    filter_backends = [DjangoFilterBackend, drf_filters.OrderingFilter]
    ordering_fields = ["created_datetime", "amount"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Transaction.objects.none()
        return list_transactions(user=self.request.user)

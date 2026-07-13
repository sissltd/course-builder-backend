from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters as drf_filters
from rest_framework.generics import CreateAPIView, ListAPIView, RetrieveAPIView

from api.users.permissions import IsCourseCreatorRole
from api.wallet.filters import TransactionFilter
from api.wallet.models import Transaction
from api.wallet.serializers import TransactionSerializer, WalletSerializer, WithdrawalRequestSerializer
from api.wallet.services import wallet_service


class WalletDetailView(RetrieveAPIView):
    """The current user's wallet balance, auto-provisioned on first access."""

    permission_classes = [IsCourseCreatorRole]
    serializer_class = WalletSerializer

    def get_object(self):
        return wallet_service.get_or_create_wallet(user=self.request.user)


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
        return wallet_service.list_transactions(user=self.request.user)


class WithdrawalRequestCreateView(CreateAPIView):
    """Create a withdrawal request against the current user's wallet."""

    permission_classes = [IsCourseCreatorRole]
    serializer_class = WithdrawalRequestSerializer

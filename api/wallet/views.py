from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters as drf_filters
from rest_framework import status
from rest_framework.generics import (
    CreateAPIView,
    DestroyAPIView,
    ListAPIView,
    ListCreateAPIView,
    RetrieveAPIView,
)
from rest_framework.response import Response
from rest_framework.views import APIView

from api.users.permissions import IsCourseCreatorRole
from api.wallet.filters import TransactionFilter
from api.wallet.models import PayoutAccount, Transaction
from api.wallet.serializers import (
    PayoutAccountCreateSerializer,
    PayoutAccountSerializer,
    TransactionSerializer,
    WalletSerializer,
    WithdrawalConfirmSerializer,
    WithdrawalRequestCreateSerializer,
)
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


class PayoutAccountListCreateView(ListCreateAPIView):
    """List the current user's payout accounts, or add a new one
    (Settings -> Payment -> Add account)."""

    permission_classes = [IsCourseCreatorRole]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return PayoutAccount.objects.none()
        return wallet_service.list_payout_accounts(user=self.request.user)

    def get_serializer_class(self):
        if self.request.method == "POST":
            return PayoutAccountCreateSerializer
        return PayoutAccountSerializer


class PayoutAccountDestroyView(DestroyAPIView):
    """Remove one of the current user's payout accounts."""

    permission_classes = [IsCourseCreatorRole]
    serializer_class = PayoutAccountSerializer

    def get_queryset(self):
        return wallet_service.list_payout_accounts(user=self.request.user)


class WithdrawalRequestCreateView(CreateAPIView):
    """Step 1 of withdrawal: request an amount against a payout account.

    Emails an OTP the caller must submit to WithdrawalConfirmView; no funds
    move until that's confirmed.
    """

    permission_classes = [IsCourseCreatorRole]
    serializer_class = WithdrawalRequestCreateSerializer


class WithdrawalConfirmView(APIView):
    """Step 2 of withdrawal: confirm the OTP sent for a pending
    WithdrawalRequest, creating the resulting Transaction."""

    permission_classes = [IsCourseCreatorRole]
    serializer_class = WithdrawalConfirmSerializer  # for schema generation only

    def post(self, request, withdrawal_request_id):
        serializer = WithdrawalConfirmSerializer(
            data=request.data,
            context={
                "request": request,
                "withdrawal_request_id": withdrawal_request_id,
            },
        )
        serializer.is_valid(raise_exception=True)
        txn = serializer.save()
        return Response(
            TransactionSerializer(txn, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )

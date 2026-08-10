
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from rest_framework import filters as drf_filters
from rest_framework import status
from rest_framework.generics import CreateAPIView, DestroyAPIView, ListAPIView, ListCreateAPIView, RetrieveAPIView
from rest_framework.views import APIView

from api.payments.models.transaction_model import Transaction
from api.users.permissions import IsAdminOrSuperAdminRole, IsCourseCreatorRole
from api.wallet.filters import (
    AdminTransactionFilter,
    AdminWithdrawalRequestFilter,
)
from api.wallet.models import PayoutAccount, Wallet, WithdrawalRequest
from api.wallet.serializers import (
    AdminTransactionSerializer,
    AdminWalletSerializer,
    AdminWithdrawalRequestSerializer,
    PayoutAccountCreateSerializer,
    PayoutAccountSerializer,
    TransactionSerializer,
    WalletSerializer,
    WithdrawalConfirmSerializer,
    WithdrawalRequestCreateSerializer,
)
from api.wallet.services import wallet_service
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES
from shared.response.error import custom_error_response
from shared.response.success import custom_success_response

_WALLET_OWNER_EXAMPLE = {
    "id": "5a1f83c6-92b4-4e70-8d3f-1c7e6b409af2",
    "email": "chidera.nwosu@example.com",
    "first_name": "Chidera",
    "last_name": "Nwosu",
}

_ADMIN_AUTH_LINE = (
    "**Auth:** Admin or Super Admin. The creator-facing wallet endpoints are "
    "gated on the Course Creator role and 403 for admins, which is why this "
    "parallel read exists."
)


class WalletDetailView(RetrieveAPIView):
    """The current user's wallet balance, auto-provisioned on first access."""

    permission_classes = [IsCourseCreatorRole]
    serializer_class = WalletSerializer

    def get_object(self):
        return wallet_service.get_or_create_wallet(user=self.request.user)


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

        try:
            txn = serializer.save()
            return custom_success_response(
                data=TransactionSerializer(txn, context={"request": request}).data,
                message="Withdrawal request is being processed.",
                status=status.HTTP_202_ACCEPTED,
            )
        except Exception as e:
            return custom_error_response(
                data=None,
                message=str(e),
                status=status.HTTP_400_BAD_REQUEST,
            )



@extend_schema(
    summary="List all creator wallets",
    description=(
        "Returns every creator's wallet with its current balance, so an Admin "
        "can answer finance and support questions — 'what is this creator's "
        "balance?', 'who is owed what?' — without a database console.\n\n"
        "Called when the admin Finance screen loads.\n\n"
        f"{_ADMIN_AUTH_LINE}\n\n"
        "**Prerequisites:** None beyond holding the Admin or Super Admin "
        "role.\n\n"
        "**Important:** Read-only; there is no admin endpoint that adjusts a "
        "balance, deliberately — balances move only through "
        "`credit_wallet` (course approval) and a confirmed withdrawal. Wallets "
        "are provisioned lazily, so a creator who has never earned or opened "
        "their wallet has no row here at all. Unlike the creator's own wallet "
        "view this omits `total_earned`/`pending_balance`, which cost an "
        "aggregate query per row. Paginated, most recently updated first."
    ),
    tags=["Admin — Wallets"],
    responses={
        200: OpenApiResponse(
            response=AdminWalletSerializer(many=True),
            description="Creator wallets.",
            examples=[
                OpenApiExample(
                    name="Success",
                    value=[
                        {
                            "id": "9d4c1b77-6e35-4a82-b0f9-3c8a7d215e60",
                            "user": _WALLET_OWNER_EXAMPLE,
                            "balance": "420.00",
                            "currency": "USD",
                            "updated_datetime": "2026-08-05T16:11:44.230Z",
                        }
                    ],
                )
            ],
        ),
        **STANDARD_ERROR_RESPONSES["auth"],
        **STANDARD_ERROR_RESPONSES["permission"],
        **STANDARD_ERROR_RESPONSES["server"],
    },
)
class AdminWalletListView(ListAPIView):
    """Every creator wallet, for the admin finance view."""

    permission_classes = [IsAdminOrSuperAdminRole]
    serializer_class = AdminWalletSerializer
    filter_backends = [drf_filters.OrderingFilter]
    ordering_fields = ["balance", "updated_datetime"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Wallet.objects.none()
        return wallet_service.list_all_wallets(actor=self.request.user)


@extend_schema(
    summary="List all wallet transactions",
    description=(
        "Returns the platform-wide transaction ledger — every credit from a "
        "course approval and every debit from a confirmed withdrawal, with the "
        "creator each belongs to. This is what an Admin reconciles against "
        "when a creator disputes a balance.\n\n"
        "Called from the admin Finance screen, and from a creator's detail "
        "panel with `?user=` applied.\n\n"
        f"{_ADMIN_AUTH_LINE}\n\n"
        "**Prerequisites:** None beyond holding the Admin or Super Admin "
        "role.\n\n"
        "**Important:** Filter with `?user=<uuid>`, `?type=CREDIT|DEBIT`, or "
        "`?status=PENDING|COMPLETED|FAILED`. Note that a withdrawal debit is "
        "written as `PENDING` and nothing in the platform moves it to "
        "`COMPLETED` or `FAILED` yet — payout settlement is not implemented — "
        "so a long-standing `PENDING` debit is expected, not a stuck record. "
        "Paginated, newest first."
    ),
    tags=["Admin — Wallets"],
    responses={
        200: OpenApiResponse(
            response=AdminTransactionSerializer(many=True),
            description="Wallet transactions, newest first.",
            examples=[
                OpenApiExample(
                    name="Success",
                    value=[
                        {
                            "user": _WALLET_OWNER_EXAMPLE,
                            "id": "1f6b2c94-8a70-4d31-9e52-7b0c4d8a6135",
                            "reference": "TXN-4A9C13E7B052",
                            "course": {
                                "id": "7d2f4b18-3c9a-4e51-b8f0-1a6c5d3e9b74",
                                "title": "Introduction to Systems Design",
                            },
                            "amount": "150.00",
                            "fee": "0.00",
                            "type": "CREDIT",
                            "status": "COMPLETED",
                            "description": (
                                "Course 'Introduction to Systems Design' approved"
                            ),
                            "recipient_account_name": "",
                            "recipient_account_number": "",
                            "recipient_provider_name": "",
                            "created_datetime": "2026-08-01T10:05:19.774Z",
                        }
                    ],
                )
            ],
        ),
        **STANDARD_ERROR_RESPONSES["auth"],
        **STANDARD_ERROR_RESPONSES["permission"],
        **STANDARD_ERROR_RESPONSES["server"],
    },
)
class AdminTransactionListView(ListAPIView):
    """The platform-wide transaction ledger, for admins."""

    permission_classes = [IsAdminOrSuperAdminRole]
    serializer_class = AdminTransactionSerializer
    filterset_class = AdminTransactionFilter
    filter_backends = [DjangoFilterBackend, drf_filters.OrderingFilter]
    ordering_fields = ["created_datetime", "amount"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Transaction.objects.none()
        return wallet_service.list_all_transactions(actor=self.request.user)


@extend_schema(
    summary="List all withdrawal requests",
    description=(
        "Returns every withdrawal request with its creator, destination "
        "account, and status. This is the closest thing the platform has to a "
        "payout worklist: it shows who has asked to be paid, how much, and to "
        "which account.\n\n"
        "Called from the admin Payouts screen.\n\n"
        f"{_ADMIN_AUTH_LINE}\n\n"
        "**Prerequisites:** None beyond holding the Admin or Super Admin "
        "role.\n\n"
        "**Important:** Read-only, and there is currently no endpoint that "
        "settles or fails a withdrawal — confirming one debits the creator's "
        "balance and leaves a `PENDING` transaction that nothing advances, so "
        "treat `CONFIRMED` here as 'awaiting a manual bank transfer', not "
        "'paid'. Requests left at `PENDING_CONFIRMATION` are ones where the "
        "creator never entered their OTP; they are never expired "
        "automatically. Filter with `?status=` and `?user=`. Paginated, "
        "newest first."
    ),
    tags=["Admin — Wallets"],
    responses={
        200: OpenApiResponse(
            response=AdminWithdrawalRequestSerializer(many=True),
            description="Withdrawal requests, newest first.",
            examples=[
                OpenApiExample(
                    name="Success",
                    value=[
                        {
                            "id": "b8e0a25d-4713-49cf-8a6b-05d29e13c7f4",
                            "user": _WALLET_OWNER_EXAMPLE,
                            "amount": "200.00",
                            "status": "CONFIRMED",
                            "payout_account": {
                                "id": "6c31f8a0-92db-4e57-b14a-83f7c0d25e69",
                                "account_type": "LOCAL",
                                "provider_name": "Access Bank",
                                "account_number": "0123456789",
                                "account_name": "Chidera Nwosu",
                                "is_default": True,
                                "created_datetime": "2026-07-02T12:41:07.556Z",
                            },
                            "transaction_reference": "TXN-77B1E4C0A93D",
                            "confirmed_at": "2026-08-05T09:18:33.021Z",
                            "created_datetime": "2026-08-05T09:12:50.404Z",
                        }
                    ],
                )
            ],
        ),
        **STANDARD_ERROR_RESPONSES["auth"],
        **STANDARD_ERROR_RESPONSES["permission"],
        **STANDARD_ERROR_RESPONSES["server"],
    },
)
class AdminWithdrawalRequestListView(ListAPIView):
    """Every withdrawal request across all creators, for admins."""

    permission_classes = [IsAdminOrSuperAdminRole]
    serializer_class = AdminWithdrawalRequestSerializer
    filterset_class = AdminWithdrawalRequestFilter
    filter_backends = [DjangoFilterBackend, drf_filters.OrderingFilter]
    ordering_fields = ["created_datetime", "amount"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return WithdrawalRequest.objects.none()
        return wallet_service.list_all_withdrawal_requests(actor=self.request.user)

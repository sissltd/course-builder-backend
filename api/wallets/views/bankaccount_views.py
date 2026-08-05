"""
Bank account management endpoints.
"""

import logging
from typing import ClassVar, cast

from django.contrib.auth import get_user_model
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView

from api.users.permissions import IsAdminRole
from api.wallets.docs.bankaccount_docs import (
    BANK_ACCOUNT_CREATE_DOCS,
    BANK_ACCOUNT_DELETE_DOCS,
    BANK_ACCOUNT_DETAIL_DOCS,
    BANK_ACCOUNT_LIST_DOCS,
    BANK_ACCOUNT_SUSPEND_DOCS,
    BANK_ACCOUNT_VERIFY_DOCS,
    BANK_LIST_DOCS,
)
from api.wallets.models.bankaccount_models import BankAccount
from api.wallets.serializers.bankaccount_serializers import (
    BankAccountCreateSerializer,
    BankAccountListSerializer,
    BankAccountVerifySerializer,
)
from api.wallets.services.bankaccount_services import (
    AccountDetailsError,
    create_bank_account,
    delete_bank_account,
    get_bank_account_list,
    set_default_bank_account,
    suspend_bank_account,
)
from shared.response.error import custom_error_response
from shared.response.success import custom_success_response
from shared.services.paystack_service import PaystackService
from shared.utils.client_meta import client_meta

User = get_user_model()

logger = logging.getLogger(__name__)


@extend_schema_view(
    get=extend_schema(**BANK_ACCOUNT_LIST_DOCS),
    post=extend_schema(**BANK_ACCOUNT_CREATE_DOCS),
)
class BankAccountListCreateView(APIView):
    permission_classes: ClassVar = [IsAuthenticated]

    def get(self, request):
        """List bank accounts for the authenticated user."""

        user = request.user
        bank_accounts = get_bank_account_list(user)

        return custom_success_response(
            message="Retrieved successfully",
            data=BankAccountListSerializer(bank_accounts, many=True).data,
            status=status.HTTP_200_OK,
        )

    def post(self, request):
        """Create a new bank account for the authenticated user."""
        serializer = BankAccountCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ip, ua = client_meta(request)

        try:
            account = create_bank_account(
                user=request.user,
                validated_data=serializer.validated_data,
                ip=ip,
                ua=ua,
            )

        except AccountDetailsError as e:
            return custom_error_response(
                message=str(e),
                status=status.HTTP_400_BAD_REQUEST,
            )
        return custom_success_response(
            message="Bank account added successfully",
            data={"bank_account_id": str(account.id)},
            status=status.HTTP_201_CREATED,
        )


class BankAccountDetailView(APIView):
    permission_classes: ClassVar = [IsAuthenticated]

    @extend_schema(**BANK_ACCOUNT_DETAIL_DOCS)
    def get(self, request, pk):
        """Retrieve a specific bank account for the authenticated user."""
        user = request.user
        try:
            bank_account = BankAccount.objects.get(id=pk, user=user, is_deleted=False)
        except BankAccount.DoesNotExist:
            return custom_error_response(
                message="Bank account not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        return custom_success_response(
            message="Retrieved successfully",
            data=BankAccountListSerializer(bank_account).data,
            status=status.HTTP_200_OK,
        )
    
    @extend_schema(**BANK_ACCOUNT_DELETE_DOCS)
    def delete(self, request, pk):
        """Delete a specific bank account for the authenticated user."""
        user = request.user
        try:
            delete_bank_account(user, pk, *client_meta(request))
        except BankAccount.DoesNotExist:
            return custom_error_response(
                message="Bank account not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        return custom_success_response(
            message="Bank account deleted successfully",
            status=status.HTTP_204_NO_CONTENT,
        )
        

class BankAccountSetDefaultView(APIView):
    permission_classes: ClassVar = [IsAuthenticated]

    # @extend_schema(**BANK_ACCOUNT_SET_DEFAULT_DOCS)
    def post(self, request, pk):
        """Set a specific bank account as the default for the authenticated user."""
        user = request.user
        try:
            set_default_bank_account(user, pk, *client_meta(request))
        except BankAccount.DoesNotExist:
            return custom_error_response(
                message="Bank account not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        return custom_success_response(
            message="Bank account set as default successfully",
            status=status.HTTP_200_OK,
        )
        

@extend_schema(**BANK_ACCOUNT_SUSPEND_DOCS)
class BankAccountSuspendView(APIView):
    permission_classes: ClassVar = [IsAdminRole]

    def post(self, request, pk):
        """Suspend a specific bank account for the authenticated user."""
        user = request.user
        try:
            suspend_bank_account(user, pk, *client_meta(request))
        except BankAccount.DoesNotExist:
            return custom_error_response(
                message="Bank account not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        return custom_success_response(
            message="Bank account suspended successfully",
            status=status.HTTP_200_OK,
        )


@extend_schema(**BANK_ACCOUNT_VERIFY_DOCS)
class VerifyBankAccountView(APIView):
    permission_classes: ClassVar[list] = [AllowAny]
    serializer_class = BankAccountVerifySerializer

    def post(self, request):
        serializer = BankAccountVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = cast(dict, serializer.validated_data)
        account_number = data.get("account_number")
        bank_code = data.get("bank_code")

        if not account_number or not bank_code:
            return custom_error_response(
                status=status.HTTP_400_BAD_REQUEST, message="Both account number and bank code are required"
            )

        try:
            verification_result = PaystackService.resolve_bank(account_number=account_number, bank_code=bank_code)
            return custom_success_response(
                status=status.HTTP_200_OK,
                message="Bank account verified successfully",
                data=verification_result,
            )
        except Exception as exc:
            logger.error(exc)
            return custom_error_response(
                status=status.HTTP_400_BAD_REQUEST,
                message="Bank account verification failed",
                technical_message=str(exc),
            )


@extend_schema(**BANK_LIST_DOCS)
class BankListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        """Returns a list of bank names and codes, as returned from Paystack. This uses Redis cache with a 24 hour expiry to minimize calls to Paystack API. The endpoint is public and requires no authentication."""

        banks_result = PaystackService.get_banks()
        bank_list = banks_result["data"]
        data = [{"name": bnk["name"], "code": bnk["code"]} for bnk in bank_list]

        return custom_success_response(
            message="Processed successfully",
            data=data,
            status=status.HTTP_200_OK,
        )

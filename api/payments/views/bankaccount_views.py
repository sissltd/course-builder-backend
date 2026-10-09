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

from api.authorization import codenames
from api.authorization.permissions import Perm
from api.payments.docs.bankaccount_docs import (
    BANK_ACCOUNT_CREATE_DOCS,
    BANK_ACCOUNT_DELETE_DOCS,
    BANK_ACCOUNT_DETAIL_DOCS,
    BANK_ACCOUNT_LIST_DOCS,
    BANK_ACCOUNT_REMOVE_SUSPENSION_DOCS,
    BANK_ACCOUNT_SET_DEFAULT_DOCS,
    BANK_ACCOUNT_SUSPEND_DOCS,
    BANK_ACCOUNT_VERIFY_DOCS,
    BANK_LIST_DOCS,
)
from api.payments.models.bankaccount_models import BankAccount
from api.payments.serializers.bankaccount_serializers import (
    BankAccountCreateSerializer,
    BankAccountListSerializer,
    BankAccountVerifySerializer,
)
from api.payments.services.bankaccount_services import (
    AccountDetailsError,
    create_bank_account,
    delete_bank_account,
    get_bank_account_list,
    remove_bank_account_suspension,
    set_default_bank_account,
    suspend_bank_account,
    verify_account_details,
)
from shared.response.error import custom_error_response
from shared.response.success import custom_success_response
from shared.services.payment_services.provider import get_payment_provider
from shared.utils.client_meta import client_meta

User = get_user_model()

logger = logging.getLogger(__name__)


@extend_schema_view(
    get=extend_schema(**BANK_ACCOUNT_LIST_DOCS, operation_id="payout_accounts_list"),
    post=extend_schema(**BANK_ACCOUNT_CREATE_DOCS),
)
class BankAccountListCreateView(APIView):
    permission_classes: ClassVar = [IsAuthenticated]

    def get(self, request):
        """
        /api/v1/bank-accounts/
        if user is admin, return all bank accounts, else return only the bank accounts for the authenticated user.
        """

        user = request.user
        bank_accounts = get_bank_account_list(user)

        return custom_success_response(
            message="Retrieved successfully",
            data=BankAccountListSerializer(bank_accounts, many=True).data,
            status=status.HTTP_200_OK,
        )

    def post(self, request):
        """`/api/v1/payout-accounts/`
        
        Create a new bank account for the authenticated user.
        If the user already has a bank account with the same account number and 
        bank code, it will be updated (set as the default) instead of creating a new one.
        If the existing account is suspended, we report same
        """
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
        except Exception as e:
            logger.error(f"Error creating bank account: {e}")
            return custom_error_response(
                message=f"An error occurred while creating the bank account: {e}.",
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return custom_success_response(
            message="Bank account added successfully",
            data={"bank_account": BankAccountListSerializer(account).data},
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


@extend_schema(**BANK_ACCOUNT_SET_DEFAULT_DOCS, request=None)
class BankAccountSetDefaultView(APIView):
    """/api/v1/payout-accounts/{id}/default/
    
    Creator setting a default payout account. A suspended bank account cannot be set as default.
    """
    permission_classes: ClassVar = [IsAuthenticated]

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
        except AccountDetailsError as e:
            return custom_error_response(
                message=str(e),
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            return custom_error_response(
                message=str(e),
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return custom_success_response(
            message="Bank account set as default successfully",
            status=status.HTTP_200_OK,
        )


@extend_schema(**BANK_ACCOUNT_SUSPEND_DOCS, request=None)
class BankAccountSuspendView(APIView):
    """/api/v1/payout-accounts/{id}/suspend/

    Admin suspending a user's bank account. The account owner will be notified
    via their activity log, and the admin performing the action is recorded as
    the actor.
    """
    # Admin tier without the Approver, matching every other money surface
    # (wallet admin, KYC review): approving courses is no reason to be able
    # to freeze someone's payouts.
    permission_classes: ClassVar = [Perm(codenames.CREATORS_SUSPEND)]

    def post(self, request, pk):
        """Suspend any user's bank account - an admin moderation action."""
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
        

@extend_schema(**BANK_ACCOUNT_REMOVE_SUSPENSION_DOCS, request=None)
class BankAccountRemoveSuspensionView(APIView):
    """/api/v1/payout-accounts/{id}/remove-suspension/

    Admin removing the suspension on a user's bank account. The account owner will be notified
    via their activity log, and the admin performing the action is recorded as
    the actor.
    """
    # Admin tier without the Approver, matching every other money surface
    # (wallet admin, KYC review): approving courses is no reason to be able
    # to freeze someone's payouts.
    permission_classes: ClassVar = [Perm(codenames.CREATORS_SUSPEND)]

    def post(self, request, pk):
        """Remove the suspension on any user's bank account - an admin moderation action."""
        user = request.user
        try:
            remove_bank_account_suspension(user, pk, *client_meta(request))
        except BankAccount.DoesNotExist:
            return custom_error_response(
                message="Bank account not found.",
                status=status.HTTP_404_NOT_FOUND,
            )

        return custom_success_response(
            message="Bank account suspension removed successfully",
            status=status.HTTP_200_OK,
        )


@extend_schema(**BANK_ACCOUNT_VERIFY_DOCS, auth=[{}])
class VerifyBankAccountView(APIView):
    """/api/v1/payout-accounts/verify/

    Calls a payment provider to verify the given bank account details. 
    Providers' test environment/sandbox typically returns fictitious account 
    names, e.g.: `Ajadi Jackson` from Flutterwave and `Test Account` from Paystack
    """
    authentication_classes = []  # public: a stale token must not 401 this
    permission_classes: ClassVar[list] = [AllowAny]
    serializer_class = BankAccountVerifySerializer

    def post(self, request):
        serializer = BankAccountVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = cast(dict, serializer.validated_data)
        account_number = data.get("account_number")
        bank_code = data.get("bank_code")


        try:
            resp_data = verify_account_details(account_number=account_number, bank_code=bank_code)
            return custom_success_response(
                status=status.HTTP_200_OK,
                message="Bank account verified successfully",
                data=resp_data
            )
        except Exception as exc:
            logger.error(exc)
            return custom_error_response(
                status=status.HTTP_400_BAD_REQUEST,
                message=f"Bank account verification failed: {exc!s}",
                technical_message=str(exc),
            )


@extend_schema(**BANK_LIST_DOCS, auth=[{}])
class BankListView(APIView):
    authentication_classes = []  # public: a stale token must not 401 this
    permission_classes = [AllowAny]

    def get(self, request):
        """Returns a list of bank names and codes, as returned from Paystack. This uses Redis cache with a 24 hour expiry to minimize calls to Paystack API. The endpoint is public and requires no authentication."""

        processor = get_payment_provider()
        banks_result = processor.get_banks()

        return custom_success_response(
            message="Processed successfully",
            data=sorted(banks_result, key=lambda x: x["name"].strip()),
            status=status.HTTP_200_OK,
        )

import re
from decimal import Decimal

from django.core import mail
from rest_framework import status
from rest_framework.test import APITestCase

from api.courses.tests.factories import make_user
from api.users.enums import KYCStatus, UserRole
from api.users.models import KYCVerification
from api.wallet.services import wallet_service


def _approve_kyc(user):
    KYCVerification.objects.create(
        user=user,
        country_of_issue="NG",
        document_type="NATIONAL_ID",
        id_number="12345",
        status=KYCStatus.APPROVED,
    )


class WalletApiTests(APITestCase):
    def setUp(self):
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.admin = make_user(role=UserRole.ADMIN)

    def test_creator_can_retrieve_auto_provisioned_wallet(self):
        self.client.force_authenticate(self.creator)

        response = self.client.get("/api/v1/wallet/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["balance"], "0.00")
        self.assertEqual(response.data["total_earned"], Decimal("0.00"))
        self.assertEqual(response.data["pending_balance"], Decimal("0.00"))

    def test_non_creator_role_forbidden(self):
        self.client.force_authenticate(self.admin)

        response = self.client.get("/api/v1/wallet/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_unauthenticated_forbidden(self):
        response = self.client.get("/api/v1/wallet/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_transaction_list_filter_by_type(self):
        wallet_service.credit_wallet(user=self.creator, amount=Decimal("30.00"))
        self.client.force_authenticate(self.creator)

        response = self.client.get("/api/v1/transactions/", {"type": "CREDIT"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["data"]["results"]), 1)


class PayoutAccountApiTests(APITestCase):
    def setUp(self):
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.client.force_authenticate(self.creator)

    def test_create_and_list_payout_account(self):
        response = self.client.post(
            "/api/v1/payout-accounts/",
            {
                "account_type": "LOCAL",
                "provider_name": "Access Bank",
                "account_number": "1234567890",
                "account_name": "Test User",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["is_default"])

        list_response = self.client.get("/api/v1/payout-accounts/")
        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(list_response.data["data"]["results"]), 1)

    def test_delete_payout_account(self):
        account = wallet_service.create_payout_account(
            user=self.creator,
            account_type="LOCAL",
            provider_name="Access Bank",
            account_number="1234567890",
            account_name="Test User",
        )

        response = self.client.delete(f"/api/v1/payout-accounts/{account.id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)


class WithdrawalApiTests(APITestCase):
    def setUp(self):
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        _approve_kyc(self.creator)
        wallet_service.credit_wallet(user=self.creator, amount=Decimal("100.00"))
        self.payout_account = wallet_service.create_payout_account(
            user=self.creator,
            account_type="LOCAL",
            provider_name="Access Bank",
            account_number="1234567890",
            account_name="Test User",
        )
        self.client.force_authenticate(self.creator)

    def test_withdrawal_above_threshold_succeeds_and_confirm_completes_it(self):
        request_response = self.client.post(
            "/api/v1/withdrawals/",
            {"amount": "60.00", "payout_account": str(self.payout_account.id)},
            format="json",
        )
        self.assertEqual(request_response.status_code, status.HTTP_201_CREATED)
        withdrawal_request_id = request_response.data["id"]
        code = re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)

        confirm_response = self.client.post(
            f"/api/v1/withdrawals/{withdrawal_request_id}/confirm/",
            {"code": code},
            format="json",
        )
        self.assertEqual(confirm_response.status_code, status.HTTP_200_OK)
        self.assertEqual(confirm_response.data["type"], "DEBIT")

    def test_withdrawal_below_threshold_rejected(self):
        response = self.client.post(
            "/api/v1/withdrawals/",
            {"amount": "5.00", "payout_account": str(self.payout_account.id)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_withdrawal_without_kyc_rejected(self):
        other_creator = make_user(role=UserRole.COURSE_CREATOR)
        wallet_service.credit_wallet(user=other_creator, amount=Decimal("100.00"))
        other_payout_account = wallet_service.create_payout_account(
            user=other_creator,
            account_type="LOCAL",
            provider_name="Access Bank",
            account_number="1234567890",
            account_name="Other User",
        )
        self.client.force_authenticate(other_creator)

        response = self.client.post(
            "/api/v1/withdrawals/",
            {"amount": "60.00", "payout_account": str(other_payout_account.id)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

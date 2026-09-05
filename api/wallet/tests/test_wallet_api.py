import re
from decimal import Decimal
from unittest.mock import patch

from django.core import mail
from rest_framework import status
from rest_framework.test import APITestCase

from api.courses.tests.factories import make_user
from api.platform.enums import PaymentProcessors
from api.platform.models import PlatformSettings
from api.users.enums import KYCStatus, UserRole
from api.users.models import KYCVerification
from api.wallet.services import wallet_service
from shared.utils.encryption import decrypt_field


def _approve_kyc(user):
    KYCVerification.objects.create(
        user=user,
        country_of_issue="NG",
        document_type="NATIONAL_ID",
        id_number="12345",
        status=KYCStatus.APPROVED,
        date_of_birth="1990-01-01",
    )


class WalletApiTests(APITestCase):
    def setUp(self):
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.admin = make_user(role=UserRole.ADMIN)

        self.flutterwave_recipient_patcher = patch(
            "shared.services.flutterwave_service.FlutterwaveService.get_recipient_id", return_value="RCP_TEST_123"
        )
        self.transfer_task_patcher = patch("api.wallet.services.wallet_service.dispatch_transfer_task.delay")

        self.processor_patcher = patch.object(PlatformSettings, "payment_processor", PaymentProcessors.FLUTTERWAVE)
        self.flutterwave_recipient_patcher.start()
        self.transfer_task_patcher.start()
        self.processor_patcher.start()

    def tearDown(self):
        self.flutterwave_recipient_patcher.stop()
        self.transfer_task_patcher.stop()
        self.processor_patcher.stop()

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


class WithdrawalApiTests(APITestCase):
    def setUp(self):
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        _approve_kyc(self.creator)
        wallet_service.credit_wallet(user=self.creator, amount=Decimal("100.00"))

        self.flutterwave_recipient_patcher = patch(
            "shared.services.flutterwave_service.FlutterwaveService.get_recipient_id",
            return_value="RCP_TEST_123",
        )
        self.decrypt_patcher = patch(
            "api.wallet.serializers.decrypt_field",
            side_effect=lambda value: value,
        )
        self.transfer_task_patcher = patch("api.wallet.services.wallet_service.dispatch_transfer_task.delay")

        self.flutterwave_recipient_patcher.start()
        self.decrypt_patcher.start()
        self.transfer_task_patcher.start()

        self.payout_account = wallet_service.create_payout_account(
            user=self.creator,
            account_type="LOCAL",
            bank_name="Access Bank",
            account_number="1234567890",
            account_name="Test User",
            bank_code="058",
        )
        self.client.force_authenticate(self.creator)

    def tearDown(self):
        self.transfer_task_patcher.stop()
        self.flutterwave_recipient_patcher.stop()
        self.decrypt_patcher.stop()

    def test_withdrawal_above_threshold_succeeds_and_confirm_completes_it(self):
        request_response = self.client.post(
            "/api/v1/withdrawals/",
            {"amount": "60.00", "payout_account": str(self.payout_account.id)},
            format="json",
        )
        self.assertEqual(request_response.status_code, status.HTTP_201_CREATED)
        withdrawal_request_id = request_response.data["id"]
        match = re.search(r"\b(\d{6})\b", str(mail.outbox[-1].body))
        if match is None:
            self.fail("Expected withdrawal OTP email to contain a 6-digit code.")
        code = match.group(1)

        confirm_response = self.client.post(
            f"/api/v1/withdrawals/{withdrawal_request_id}/confirm/",
            {"code": code},
            format="json",
        )
        self.assertEqual(confirm_response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(confirm_response.data["data"]["type"], "DEBIT")

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
            bank_name="Access Bank",
            account_number="1234567890",
            account_name="Other User",
            bank_code="058",
        )
        self.client.force_authenticate(other_creator)

        response = self.client.post(
            "/api/v1/withdrawals/",
            {"amount": "60.00", "payout_account": str(other_payout_account.id)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class AdminWalletApiTests(APITestCase):
    def setUp(self):
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.admin = make_user(role=UserRole.ADMIN)
        _approve_kyc(self.creator)
        wallet_service.credit_wallet(user=self.creator, amount=Decimal("100.00"))

    def test_creator_cannot_read_the_admin_wallet_list(self):
        self.client.force_authenticate(self.creator)

        response = self.client.get("/api/v1/admin/wallets/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_see_a_creators_wallet(self):
        """Regression: every creator-facing wallet route is gated on
        IsCourseCreatorRole, so an admin used to be 403'd from all of them and
        had no way to answer 'what is this creator's balance?'."""

        self.client.force_authenticate(self.admin)

        response = self.client.get("/api/v1/admin/wallets/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows = response.data["data"]["results"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["balance"], "100.00")
        self.assertEqual(rows[0]["user"]["email"], self.creator.email)

    def test_admin_transaction_ledger_includes_the_owner(self):
        self.client.force_authenticate(self.admin)

        response = self.client.get("/api/v1/admin/transactions/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows = response.data["data"]["results"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["user"]["email"], self.creator.email)
        self.assertEqual(rows[0]["type"], "CREDIT")

    def test_admin_transaction_ledger_filters_by_user(self):
        other = make_user(role=UserRole.COURSE_CREATOR)
        wallet_service.credit_wallet(user=other, amount=Decimal("40.00"))
        self.client.force_authenticate(self.admin)

        response = self.client.get(
            "/api/v1/admin/transactions/", {"user": str(other.id)}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows = response.data["data"]["results"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["user"]["email"], other.email)

    def test_admin_sees_withdrawal_requests_with_destination_account(self):
        payout_account = wallet_service.create_payout_account(
            user=self.creator,
            account_type="LOCAL",
            bank_name="Access Bank",
            account_number="1234567890",
            account_name="Test User",
            bank_code="058",
        )
        wallet_service.request_withdrawal(
            user=self.creator,
            amount=Decimal("60.00"),
            payout_account_id=payout_account.id,
        )
        self.client.force_authenticate(self.admin)

        response = self.client.get("/api/v1/admin/withdrawals/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows = response.data["data"]["results"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "PENDING_CONFIRMATION")
        self.assertEqual(decrypt_field(rows[0]["payout_account"]["account_number"]), "1234567890")
        self.assertEqual(rows[0]["user"]["email"], self.creator.email)

    def test_admin_wallet_list_is_read_only(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post("/api/v1/admin/wallets/", {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

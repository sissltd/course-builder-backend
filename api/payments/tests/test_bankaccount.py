"""Coverage for bank account listing, management, and public verification endpoints.

The suite mirrors the category API test style: access rules first, then endpoint
behaviour and edge-case handling.
"""

from unittest.mock import patch

from rest_framework import status
from rest_framework.test import APITestCase

from api.courses.tests.factories import make_user
from api.payments.models.bankaccount_models import BankAccount
from api.payments.services.bankaccount_services import AccountDetailsError
from api.users.enums import UserRole
from shared.utils.encryption import encrypt_field

LIST_URL = "/api/v1/payout-accounts/"
VERIFY_URL = "/api/v1/payout-accounts/verify/"
BANKS_URL = "/api/v1/banks/"

VALID_PAYLOAD = {
    "account_name": "Test User",
    "account_number": "0123456789",
    "bank_code": "058",
    "is_default": True,
    "account_type": "Local Account",
}


def detail_url(account):
    return f"/api/v1/payout-accounts/{account.id}/"


def default_url(account):
    return f"/api/v1/payout-accounts/{account.id}/default/"


def suspend_url(account):
    return f"/api/v1/payout-accounts/{account.id}/suspend/"


def make_bank_account(*, user, account_number="0123456789", **kwargs):
    defaults = {
        "user": user,
        "bank_name": "Access Bank",
        "account_name": "Test User",
        "account_number": encrypt_field(account_number),
        "bank_code": "044",
        "is_default": False,
    }
    defaults.update(kwargs)
    return BankAccount.objects.create(**defaults)


class BankAccountReadAccessTests(APITestCase):
    def setUp(self):
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.decrypt_patcher = patch(
            "api.payments.serializers.bankaccount_serializers.decrypt_field",
            side_effect=lambda value: value,
        )
        self.decrypt_patcher.start()

    def tearDown(self):
        self.decrypt_patcher.stop()

    def test_list_requires_authentication(self):
        response = self.client.get(LIST_URL)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_authenticated_user_lists_only_owned_accounts(self):
        mine = make_bank_account(
            user=self.creator, paystack_recipient_code="RCP_1234567890"
        )
        make_bank_account(user=make_user(), paystack_recipient_code="RCP_0987654321")
        self.client.force_authenticate(self.creator)

        response = self.client.get(LIST_URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["data"]), 1)
        self.assertEqual(response.data["data"][0]["id"], str(mine.id))

    def test_admin_lists_all_accounts(self):
        make_bank_account(user=self.creator, paystack_recipient_code="RCP_1234567890")
        make_bank_account(user=make_user(), paystack_recipient_code="RCP_0987654321")
        self.client.force_authenticate(make_user(role=UserRole.ADMIN))

        response = self.client.get(LIST_URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["data"]), 2)

    def test_detail_returns_owned_account(self):
        account = make_bank_account(
            user=self.creator, paystack_recipient_code="RCP_1234567890"
        )
        self.client.force_authenticate(self.creator)

        response = self.client.get(detail_url(account))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["id"], str(account.id))

    def test_detail_of_other_users_account_returns_404(self):
        account = make_bank_account(
            user=make_user(), paystack_recipient_code="RCP_0987654321"
        )
        self.client.force_authenticate(self.creator)

        response = self.client.get(detail_url(account))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class BankAccountCreateTests(APITestCase):
    def setUp(self):
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.decrypt_patcher = patch(
            "api.payments.serializers.bankaccount_serializers.decrypt_field",
            side_effect=lambda value: value,
        )
        self.paystack_recipient_patcher = patch(
            "shared.services.paystack_service.PaystackService.create_transfer_recipient",
            return_value=(True, {"recipient_code": "RCP_TEST_123"}),
        )
        self.decrypt_patcher.start()
        self.paystack_recipient_patcher.start()

    def tearDown(self):
        self.decrypt_patcher.stop()
        self.paystack_recipient_patcher.stop()

    def test_create_requires_authentication(self):
        response = self.client.post(LIST_URL, VALID_PAYLOAD, format="json")

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    @patch("api.payments.views.bankaccount_views.create_bank_account")
    def test_create_persists_and_returns_created_bank_account(
        self, mock_create_bank_account
    ):
        self.client.force_authenticate(self.creator)
        created = make_bank_account(user=self.creator)
        mock_create_bank_account.return_value = created

        response = self.client.post(LIST_URL, VALID_PAYLOAD, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn("bank_account", response.data["data"])
        self.assertEqual(response.data["data"]["bank_account"]["id"], str(created.id))

    def test_create_rejects_non_digit_account_number(self):
        self.client.force_authenticate(self.creator)

        response = self.client.post(
            LIST_URL,
            {**VALID_PAYLOAD, "account_number": "ABC123"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch(
        "api.payments.views.bankaccount_views.create_bank_account",
        side_effect=AccountDetailsError("Account name does not match user profile."),
    )
    def test_create_rejects_name_that_does_not_match_profile(self, _mock_create):
        self.client.force_authenticate(self.creator)

        response = self.client.post(
            LIST_URL,
            {**VALID_PAYLOAD, "account_name": "Completely Different"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["errors"][0]["message"],
            "Account name does not match user profile.",
        )

    @patch(
        "api.payments.views.bankaccount_views.create_bank_account",
        side_effect=AccountDetailsError("This bank account is suspended."),
    )
    def test_create_returns_400_when_service_raises_account_details_error(
        self,
        _mock_create,
    ):
        self.client.force_authenticate(self.creator)

        response = self.client.post(LIST_URL, VALID_PAYLOAD, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["errors"][0]["message"],
            "This bank account is suspended.",
        )


class BankAccountMutationTests(APITestCase):
    def setUp(self):
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.client.force_authenticate(self.creator)

    def test_delete_soft_deletes_account(self):
        account = make_bank_account(
            user=self.creator, is_default=True, paystack_recipient_code="RCP_1234567890"
        )

        response = self.client.delete(detail_url(account))

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        account.refresh_from_db()
        self.assertTrue(account.is_deleted)
        self.assertFalse(account.is_default)

    def test_set_default_promotes_target_and_demotes_previous_default(self):
        first = make_bank_account(
            user=self.creator, is_default=True, paystack_recipient_code="RCP_1234567890"
        )
        second = make_bank_account(
            user=self.creator,
            account_number="1111111111",
            paystack_recipient_code="RCP_0987654321",
        )

        response = self.client.post(default_url(second), format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertFalse(first.is_default)
        self.assertTrue(second.is_default)

    def test_set_default_unknown_account_returns_404(self):
        self.client.force_authenticate(self.creator)

        response = self.client.post(
            "/api/v1/bank-accounts/00000000-0000-0000-0000-000000000000/default/",
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class BankAccountSuspendAccessTests(APITestCase):
    """Only IsAdminRole users can call suspend."""

    def test_non_admin_role_cannot_suspend(self):
        creator = make_user(role=UserRole.COURSE_CREATOR)
        account = make_bank_account(
            user=creator, is_default=True, paystack_recipient_code="RCP_1234567890"
        )
        self.client.force_authenticate(creator)

        response = self.client.post(suspend_url(account), format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_suspend_any_account(self):
        admin_ = make_user(role=UserRole.ADMIN)
        self.client.force_authenticate(admin_)
        acct_owner = make_user(role=UserRole.COURSE_CREATOR)
        account = make_bank_account(
            user=acct_owner, is_default=True, paystack_recipient_code="RCP_1234567890"
        )

        response = self.client.post(suspend_url(account), format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        account.refresh_from_db()
        self.assertTrue(account.is_suspended)


class VerifyBankAccountViewTests(APITestCase):
    def setUp(self):
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.paystack_recipient_patcher = patch(
            "shared.services.paystack_service.PaystackService.create_transfer_recipient",
            return_value=(True, {"recipient_code": "RCP_TEST_123"}),
        )
        self.paystack_recipient_patcher.start()

    def tearDown(self):
        self.paystack_recipient_patcher.stop()

    def test_public_can_verify_bank_account(self):
        with (
            patch(
                "api.payments.views.bankaccount_views.PaystackService.resolve_bank",
                return_value={"account_name": "Test User"},
            ),
            patch(
                "api.payments.views.bankaccount_views.FlutterwaveService.resolve_bank",
                return_value={"account_name": "Test User"},
            ),
        ):
            response = self.client.post(
                VERIFY_URL,
                {"account_number": "0123456789", "bank_code": "058"},
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["message"], "Bank account verified successfully")

    def test_verify_with_invalid_payload_is_rejected(self):
        response = self.client.post(
            VERIFY_URL,
            {"account_number": "0123456789"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_verify_returns_custom_error_when_provider_fails(self):
        with (
            patch(
                "api.payments.views.bankaccount_views.PaystackService.resolve_bank",
                side_effect=Exception("provider down"),
            ),
            patch(
                "api.payments.views.bankaccount_views.FlutterwaveService.resolve_bank",
                side_effect=Exception("provider down"),
            ),
        ):
            response = self.client.post(
                VERIFY_URL,
                {"account_number": "0123456789", "bank_code": "058"},
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["errors"][0]["message"], "Bank account verification failed"
        )


class BankListViewTests(APITestCase):
    def test_public_can_fetch_bank_names_and_codes(self):
        with patch(
            "api.payments.views.bankaccount_views.PaystackService.get_banks",
            return_value={
                "data": [
                    {"name": "Access Bank", "code": "044", "active": True},
                    {"name": "GTBank", "code": "058", "active": True},
                ]
            },
        ):
            response = self.client.get(BANKS_URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["data"],
            [
                {"name": "Access Bank", "code": "044"},
                {"name": "GTBank", "code": "058"},
            ],
        )

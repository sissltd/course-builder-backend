from rest_framework import status
from rest_framework.test import APITestCase

from api.mie.enums import DeveloperAccountStatus
from api.mie.models import DeveloperAccount
from api.mie.services.key_service import hash_raw_key
from api.mie.tests.factories import make_developer_account
from api.users.enums import UserRole
from api.courses.tests.factories import make_user

DEVELOPERS_URL = "/api/v1/mie/admin/developers/"


class MieDeveloperAdminApiTests(APITestCase):
    def setUp(self):
        self.superadmin = make_user(role=UserRole.SUPER_ADMIN)

    def test_requires_authentication(self):
        response = self.client.post(
            DEVELOPERS_URL, {"email": "a@b.co", "webhook_url": "https://x.io/h"}
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_non_superadmin_forbidden(self):
        self.client.force_authenticate(make_user(role=UserRole.COURSE_CREATOR))
        response = self.client.get(DEVELOPERS_URL)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class MieDeveloperRegistrationTests(APITestCase):
    def setUp(self):
        self.superadmin = make_user(role=UserRole.SUPER_ADMIN)
        self.client.force_authenticate(self.superadmin)

    def test_register_creates_pending_account_without_credentials(self):
        payload = {
            "email": "Dev@Studio.io",
            "webhook_url": "https://hooks.studio.io/mie",
            "plan_type": "BYPASS_PER_SUBMISSION",
        }

        response = self.client.post(DEVELOPERS_URL, payload)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        account = DeveloperAccount.objects.get(email="dev@studio.io")
        self.assertEqual(account.status, DeveloperAccountStatus.PENDING)
        self.assertEqual(account.api_key_hash, "")
        self.assertEqual(account.plan_type, "BYPASS_PER_SUBMISSION")
        self.assertIsNone(response.data["api_key_preview"])

    def test_duplicate_email_case_insensitive_rejected(self):
        make_developer_account(email="dev@studio.io")

        response = self.client.post(
            DEVELOPERS_URL,
            {"email": "DEV@STUDIO.IO", "webhook_url": "https://x.io/h"},
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_list_and_filter_by_status(self):
        make_developer_account()
        make_developer_account(status=DeveloperAccountStatus.APPROVED)

        response = self.client.get(DEVELOPERS_URL, {"status": "APPROVED"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["data"]["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "APPROVED")


class MieDeveloperApprovalTests(APITestCase):
    def setUp(self):
        self.superadmin = make_user(role=UserRole.SUPER_ADMIN)
        self.client.force_authenticate(self.superadmin)

    def _register_and_approve(self, email="fresh@studio.io"):
        register = self.client.post(
            DEVELOPERS_URL,
            {"email": email, "webhook_url": "https://hooks.studio.io/mie"},
        )
        account_id = register.data["id"]
        return account_id, self.client.post(f"{DEVELOPERS_URL}{account_id}/approve/")

    def test_approval_issues_one_time_key_matching_stored_hash(self):
        account_id, response = self._register_and_approve()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        raw_key = response.data["one_time_api_key"]
        self.assertTrue(raw_key.startswith("scb_live_"))
        account = DeveloperAccount.objects.get(id=account_id)
        self.assertEqual(account.status, DeveloperAccountStatus.APPROVED)
        self.assertEqual(account.api_key_hash, hash_raw_key(raw_key))
        self.assertEqual(account.approved_by, self.superadmin)
        self.assertIsNotNone(account.decided_at)

    def test_raw_key_never_appears_in_subsequent_reads(self):
        account_id, approve_response = self._register_and_approve()
        raw_key = approve_response.data["one_time_api_key"]

        detail = self.client.get(f"{DEVELOPERS_URL}{account_id}/")
        listing = self.client.get(DEVELOPERS_URL)
        serialized = str(detail.data) + str(listing.data)

        self.assertNotIn(raw_key, serialized)
        self.assertIn(detail.data["api_key_preview"], serialized)

    def test_second_approve_is_rejected_not_silent(self):
        account_id, _first = self._register_and_approve()

        second = self.client.post(f"{DEVELOPERS_URL}{account_id}/approve/")

        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST)

    def test_rejected_then_reapproved_gets_fresh_key(self):
        account_id, _first = self._register_and_approve()
        first_key_holder = DeveloperAccount.objects.get(id=account_id).api_key_hash

        self.client.post(f"{DEVELOPERS_URL}{account_id}/reject/")
        rejected = DeveloperAccount.objects.get(id=account_id)
        self.assertEqual(rejected.status, DeveloperAccountStatus.REJECTED)
        self.assertEqual(rejected.api_key_hash, "")

        reapproval = self.client.post(f"{DEVELOPERS_URL}{account_id}/approve/")

        self.assertEqual(reapproval.status_code, status.HTTP_200_OK)
        new_raw = reapproval.data["one_time_api_key"]
        self.assertIsNotNone(new_raw)
        refreshed = DeveloperAccount.objects.get(id=account_id)
        self.assertEqual(refreshed.api_key_hash, hash_raw_key(new_raw))
        self.assertNotEqual(refreshed.api_key_hash, first_key_holder)

    def test_suspend_then_reactivate_keeps_queue_history_intact(self):
        account_id, _response = self._register_and_approve()

        suspended = self.client.post(f"{DEVELOPERS_URL}{account_id}/suspend/")
        self.assertEqual(suspended.status_code, status.HTTP_200_OK)
        held = DeveloperAccount.objects.get(id=account_id)
        self.assertEqual(held.status, DeveloperAccountStatus.SUSPENDED)
        key_hash_while_suspended = held.api_key_hash

        reactivated = self.client.post(f"{DEVELOPERS_URL}{account_id}/approve/")

        self.assertEqual(reactivated.status_code, status.HTTP_200_OK)
        restored = DeveloperAccount.objects.get(id=account_id)
        self.assertEqual(restored.status, DeveloperAccountStatus.APPROVED)
        self.assertEqual(restored.api_key_hash, key_hash_while_suspended)
        self.assertIsNone(reactivated.data["one_time_api_key"])

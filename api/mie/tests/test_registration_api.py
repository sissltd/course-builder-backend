from django.core.cache import cache
from rest_framework import status
from rest_framework.test import APITestCase

from api.mie.enums import DeveloperAccountStatus
from api.mie.models import DeveloperAccount
from api.mie.tests.factories import make_developer_account

REGISTER_URL = "/api/v1/mie/v1/register/"


class DeveloperSelfRegistrationTests(APITestCase):
    def setUp(self):
        # The register route is rate-limited via the shared cache; clear it
        # so repeated runs (and the other tests here) don't trip 429s.
        cache.clear()

    def test_registration_is_open_and_creates_pending_account(self):
        response = self.client.post(
            REGISTER_URL,
            {"email": "self@studio.io", "webhook_url": "https://hooks.studio.io/mie"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], DeveloperAccountStatus.PENDING)
        account = DeveloperAccount.objects.get(email="self@studio.io")
        self.assertEqual(account.api_key_hash, "")  # no credentials until approval
        self.assertIsNone(response.data["api_key_preview"])

    def test_no_authentication_required_but_payload_still_validated(self):
        for bad in (
            {},
            {"email": "not-an-email", "webhook_url": "https://x.io/h"},
            {"email": "ok@studio.io", "webhook_url": "not-a-url"},
        ):
            with self.subTest(payload=bad):
                response = self.client.post(REGISTER_URL, bad, format="json")
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_duplicate_email_rejected_regardless_of_source(self):
        make_developer_account(email="taken@studio.io")

        response = self.client.post(
            REGISTER_URL,
            {"email": "taken@studio.io", "webhook_url": "https://x.io/h"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_email_normalized_to_lowercase(self):
        response = self.client.post(
            REGISTER_URL,
            {"email": "Mixed@Studio.io", "webhook_url": "https://x.io/h"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            DeveloperAccount.objects.filter(email="mixed@studio.io").exists()
        )

    def test_registered_account_cannot_authenticate_yet(self):
        self.client.post(
            REGISTER_URL,
            {"email": "waiting@studio.io", "webhook_url": "https://x.io/h"},
            format="json",
        )

        response = self.client.get(
            "/api/v1/mie/v1/me/", headers={"X-MIE-Api-Key": "scb_live_whatever"}
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

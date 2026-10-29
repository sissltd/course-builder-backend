"""The ingest limit follows the developer account, not the client IP.

Registration has no account yet and stays per-IP; every authenticated
developer route is keyed on the account, so developers behind one IP no
longer share a bucket, and one developer spread across IPs gets one bucket
rather than one per IP.
"""

from types import SimpleNamespace
from unittest.mock import patch

from django.core.cache import cache
from rest_framework import status
from rest_framework.test import APITestCase

from api.mie.tests.factories import make_approved_developer
from api.mie.throttling import MieDeveloperRateThrottle

INGEST_URL = "/api/v1/mie/v1/submissions/"

# A tight rate so each test reaches the limit in a couple of requests
# instead of the production 30.
TEST_RATE = {"mie_ingest": "2/min"}


class MieDeveloperThrottleKeyTests(APITestCase):
    def _key(self, account, ip):
        throttle = MieDeveloperRateThrottle()
        throttle.scope = "mie_ingest"
        request = SimpleNamespace(
            auth=account,
            META={"REMOTE_ADDR": ip, "HTTP_X_FORWARDED_FOR": ip},
        )
        return throttle.get_cache_key(request, view=None)

    def test_key_is_the_account_whatever_the_ip(self):
        account, _ = make_approved_developer()
        other, _ = make_approved_developer()

        self.assertEqual(
            self._key(account, "203.0.113.1"), self._key(account, "198.51.100.7")
        )
        self.assertNotEqual(
            self._key(account, "203.0.113.1"), self._key(other, "203.0.113.1")
        )

    def test_no_account_means_no_key(self):
        self.assertIsNone(self._key(None, "203.0.113.1"))


@patch.dict(MieDeveloperRateThrottle.THROTTLE_RATES, TEST_RATE)
class MieIngestThrottleApiTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.first_account, self.first_key = make_approved_developer()
        _, self.second_key = make_approved_developer()
        self._titles = iter(f"Throttled idea {n}" for n in range(100))

    def _submit(self, key, ip="203.0.113.1"):
        return self.client.post(
            INGEST_URL,
            {"title": next(self._titles)},
            format="json",
            HTTP_X_MIE_API_KEY=key,
            REMOTE_ADDR=ip,
        )

    def test_account_is_limited_once_its_bucket_is_used(self):
        for _ in range(2):
            self.assertEqual(self._submit(self.first_key).status_code, 201)

        response = self._submit(self.first_key)

        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertIn("Retry-After", response)

    def test_another_account_on_the_same_ip_keeps_its_own_bucket(self):
        for _ in range(2):
            self._submit(self.first_key)

        response = self._submit(self.second_key)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_changing_ip_does_not_reset_an_accounts_bucket(self):
        self._submit(self.first_key, ip="203.0.113.1")
        self._submit(self.first_key, ip="198.51.100.7")

        response = self._submit(self.first_key, ip="192.0.2.44")

        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

from rest_framework import status
from rest_framework.test import APITestCase

from api.mie.enums import SubmissionStatus, WebhookEventType
from api.mie.tests.factories import (
    make_approved_developer,
    make_decided_submission,
    make_submission,
)

QUEUE_URL = "/api/v1/mie/v1/submissions/queue/"
ME_URL = "/api/v1/mie/v1/me/"
DOCS_URL = "/api/v1/mie/v1/documentation/"


class DevSurfaceAuthBase(APITestCase):
    def setUp(self):
        self.account, self.raw_key = make_approved_developer()
        self.client.credentials(HTTP_X_MIE_API_KEY=self.raw_key)

    def _other_dev_submission(self, title="Someone Else's Idea"):
        other, _their_key = make_approved_developer()
        return make_submission(developer=other, title=title)


class QueueScopingTests(DevSurfaceAuthBase):
    def test_queue_returns_only_own_rows_even_with_matching_titles(self):
        mine = make_submission(developer=self.account, title="Shared Title")
        # The global pending-title constraint means another dev's row with
        # this title must be past review - exactly the state dedup leaves.
        self._other_dev_submission = make_decided_submission(
            title="Shared Title", approved=True
        )
        other_row = self._other_dev_submission

        response = self.client.get(QUEUE_URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["data"]["results"]
        self.assertEqual([row["id"] for row in results], [str(mine.id)])
        self.assertNotIn(str(other_row.id), [row["id"] for row in results])

    def test_developer_query_param_cannot_widen_scope(self):
        other_row = self._other_dev_submission()

        for query in ("", "?developer=", "?search=shared"):
            with self.subTest(query=query):
                response = self.client.get(f"{QUEUE_URL}{query}")
                ids = [row["id"] for row in response.data["data"]["results"]]
                self.assertNotIn(str(other_row.id), ids)

    def test_status_filter(self):
        queued = make_submission(developer=self.account)
        decided = make_decided_submission(developer=self.account, approved=True)

        response = self.client.get(QUEUE_URL, {"status": "APPROVED"})

        results = response.data["data"]["results"]
        self.assertEqual([r["id"] for r in results], [str(decided.id)])
        self.assertNotIn(str(queued.id), [r["id"] for r in results])

    def test_search_matches_title_substring_case_insensitively(self):
        hit = make_submission(developer=self.account, title="Rust For Backend Engineers")
        make_submission(developer=self.account, title="Gardening In Small Spaces")

        response = self.client.get(QUEUE_URL, {"search": "rust"})

        self.assertEqual(
            [r["id"] for r in response.data["data"]["results"]], [str(hit.id)]
        )

    def test_reference_suffix_reflects_each_state(self):
        make_submission(developer=self.account)  # P
        make_decided_submission(developer=self.account, approved=True)  # A
        make_decided_submission(developer=self.account, approved=False)  # R
        short = make_submission(
            developer=self.account, status=SubmissionStatus.DUPLICATE_EXISTING
        )  # E

        response = self.client.get(QUEUE_URL)

        suffixes = sorted(
            row["reference"].split("-")[-1] for row in response.data["data"]["results"]
        )
        self.assertEqual(suffixes, ["A", "E", "P", "R"])
        self.assertTrue(short.public_reference.endswith("-E"))

    def test_every_state_appears_including_dedup_short_circuits(self):
        make_submission(
            developer=self.account, status=SubmissionStatus.PREVIOUSLY_REJECTED
        )
        response = self.client.get(QUEUE_URL)
        statuses = {r["status"] for r in response.data["data"]["results"]}
        self.assertIn("PREVIOUSLY_REJECTED", statuses)


class MeEndpointTests(DevSurfaceAuthBase):
    def test_returns_account_snapshot_with_masked_key_and_secret(self):
        response = self.client.get(ME_URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["email"], self.account.email)
        self.assertEqual(response.data["plan_type"], "PAID_PER_SUBMISSION")
        self.assertTrue(response.data["api_key_preview"].endswith("..."))
        # The full raw key must never round-trip.
        self.assertNotIn(self.raw_key, str(response.data))
        self.assertEqual(response.data["signing_secret"], self.account.signing_secret)
        self.assertIn("webhook_url", response.data)

    def test_full_raw_key_never_leaks_from_any_field(self):
        response = self.client.get(ME_URL)
        self.assertNotIn(self.raw_key[9:], str(response.data))


class DocumentationTests(DevSurfaceAuthBase):
    def test_documents_plan_endpoints_and_all_webhook_samples(self):
        response = self.client.get(DOCS_URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        doc = response.data
        self.assertIn("plan_type", doc["plan"])
        paths = {e["path"] for e in doc["endpoints"]}
        self.assertIn("/api/v1/mie/v1/submissions/", paths)
        self.assertIn("/api/v1/mie/v1/me/", paths)
        samples = set(doc["webhooks"]["samples"].keys())
        self.assertEqual(samples, set(WebhookEventType.values))
        self.assertIn("X-MIE-Signature", str(doc["webhooks"]["signature_verification"]))
        suffixes = {s["suffix"] for s in doc["reference_scheme"]["suffixes"]}
        self.assertEqual(suffixes, {"P", "D", "E", "X", "A", "R"})

    def test_docs_reflect_the_callers_own_plan(self):
        bypass, _key = make_approved_developer(plan_type="BYPASS_ACCOUNT")
        from api.mie.services.documentation_service import build_documentation

        built = build_documentation(bypass)
        self.assertEqual(built["plan"]["plan_type"], "BYPASS_ACCOUNT")
        self.assertIn("pays out", built["plan"]["explanation"])


class PlatformSessionAccessTests(DevSurfaceAuthBase):
    def test_queue_and_me_work_via_bearer_token_too(self):
        from api.mie.services import dev_token_service

        token = dev_token_service.issue_dev_token(self.account)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        me = self.client.get(ME_URL)
        queue = self.client.get(QUEUE_URL)

        self.assertEqual(me.status_code, status.HTTP_200_OK)
        self.assertEqual(queue.status_code, status.HTTP_200_OK)

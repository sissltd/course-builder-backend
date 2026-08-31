from django.conf import settings
from rest_framework import status
from rest_framework.test import APITestCase

from api.mie.enums import MiePlanType, SubmissionStatus, WebhookEventType
from api.mie.services import webhook_dispatcher
from api.mie.services.reference import REFERENCE_SUFFIXES
from api.mie.tests.factories import (
    make_approved_developer,
    make_decided_submission,
    make_submission,
)

QUEUE_URL = "/api/v1/mie/v1/submissions/queue/"
ME_URL = "/api/v1/mie/v1/me/"
DOCS_URL = "/api/v1/mie/v1/documentation/"
DOCS_DOWNLOAD_URL = "/api/v1/mie/v1/documentation/download/"


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
    """The generated document must cover the whole integration, not a
    slice of it, and every fact in it must come from live code."""

    def test_every_documented_section_is_present(self):
        response = self.client.get(DOCS_URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            set(response.data),
            {
                "meta",
                "api",
                "your_account",
                "quickstart",
                "integration_flow",
                "authentication",
                "reference_scheme",
                "submission_lifecycle",
                "deduplication",
                "plan_and_payouts",
                "endpoints",
                "webhooks",
                "errors",
                "rate_limits",
                "pagination",
                "go_live_checklist",
                "faq",
            },
        )

    def test_documents_every_developer_reachable_endpoint(self):
        doc = self.client.get(DOCS_URL).data

        paths = {endpoint["path"] for endpoint in doc["endpoints"]}
        self.assertEqual(
            paths,
            {
                "/api/v1/mie/v1/register/",
                "/api/v1/mie/v1/submissions/",
                "/api/v1/mie/v1/submissions/queue/",
                "/api/v1/mie/v1/me/",
                "/api/v1/mie/v1/documentation/",
                "/api/v1/mie/v1/documentation/download/",
            },
        )
        for endpoint in doc["endpoints"]:
            with self.subTest(path=endpoint["path"]):
                self.assertTrue(endpoint["purpose"])
                self.assertTrue(endpoint["errors"])

    def test_every_webhook_event_type_has_a_sample_body(self):
        doc = self.client.get(DOCS_URL).data

        events = doc["webhooks"]["events"]
        self.assertEqual(
            {event["type"] for event in events}, set(WebhookEventType.values)
        )
        for event in events:
            with self.subTest(event=event["type"]):
                body = event["sample_body"]
                self.assertEqual(set(body), {"event_id", "type", "occurred_at", "submission"})
                self.assertEqual(body["type"], event["type"])
                # The wire body is what the dispatcher builds - a sample
                # with an empty submission would be documenting a bug.
                self.assertTrue(body["submission"]["reference"])
                self.assertTrue(body["submission"]["title"])
                for field in event["extra_submission_fields"]:
                    self.assertIn(field, body["submission"])

    def test_sample_bodies_match_the_shape_the_dispatcher_sends(self):
        """Guards the JSON envelope against dispatcher drift."""

        import json

        from api.mie.services.webhook_dispatcher import render_body
        from api.mie.tests.factories import make_webhook_event

        doc = self.client.get(DOCS_URL).data
        event = make_webhook_event(submission=make_submission(developer=self.account))
        on_the_wire = json.loads(render_body(event))

        for documented in doc["webhooks"]["events"]:
            with self.subTest(event=documented["type"]):
                self.assertEqual(set(documented["sample_body"]), set(on_the_wire))

    def test_reference_suffix_table_covers_every_status(self):
        doc = self.client.get(DOCS_URL).data

        documented = {row["status"]: row["suffix"] for row in doc["reference_scheme"]["suffixes"]}
        self.assertEqual(documented, {s.value: REFERENCE_SUFFIXES[s] for s in SubmissionStatus})

    def test_lifecycle_documents_every_status_with_guidance(self):
        doc = self.client.get(DOCS_URL).data

        statuses = doc["submission_lifecycle"]["statuses"]
        self.assertEqual({row["status"] for row in statuses}, set(SubmissionStatus.values))
        for row in statuses:
            with self.subTest(status=row["status"]):
                self.assertTrue(row["meaning"])
                self.assertTrue(row["action"])
                self.assertTrue(row["webhook_event"])

    def test_retry_and_replay_values_come_from_the_dispatcher(self):
        doc = self.client.get(DOCS_URL).data

        retries = doc["webhooks"]["retries"]
        self.assertEqual(retries["max_attempts"], webhook_dispatcher.MAX_ATTEMPTS)
        self.assertEqual(
            retries["backoff_seconds"], list(webhook_dispatcher.RETRY_DELAYS_SECONDS)
        )
        self.assertEqual(
            doc["webhooks"]["verification"]["replay_window_seconds"],
            webhook_dispatcher.REPLAY_WINDOW_SECONDS,
        )

    def test_rate_limits_come_from_settings(self):
        doc = self.client.get(DOCS_URL).data

        limits = " ".join(row["limit"] for row in doc["rate_limits"]["limits"])
        rates = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
        self.assertIn(rates["mie_ingest"].split("/")[0], limits)
        self.assertIn(rates["mie_register"].split("/")[0], limits)

    def test_documents_the_callers_own_account_and_plan(self):
        bypass, key = make_approved_developer(plan_type="BYPASS_ACCOUNT")
        self.client.credentials(HTTP_X_MIE_API_KEY=key)

        doc = self.client.get(DOCS_URL).data

        self.assertEqual(doc["your_account"]["email"], bypass.email)
        self.assertEqual(doc["your_account"]["webhook_url"], bypass.webhook_url)
        self.assertEqual(doc["plan_and_payouts"]["your_plan"], "BYPASS_ACCOUNT")
        self.assertFalse(doc["plan_and_payouts"]["payout_bypass_applies_to_you"])
        self.assertEqual(
            {plan["plan_type"] for plan in doc["plan_and_payouts"]["all_plans"]},
            set(MiePlanType.values),
        )

    def test_per_submission_bypass_plan_is_flagged_as_relevant(self):
        account, key = make_approved_developer(plan_type="BYPASS_PER_SUBMISSION")
        self.client.credentials(HTTP_X_MIE_API_KEY=key)

        doc = self.client.get(DOCS_URL).data

        self.assertTrue(doc["plan_and_payouts"]["payout_bypass_applies_to_you"])
        self.assertEqual(doc["your_account"]["email"], account.email)

    def test_never_leaks_the_raw_api_key(self):
        doc = self.client.get(DOCS_URL).data

        self.assertNotIn(self.raw_key[9:], str(doc))
        self.assertTrue(doc["your_account"]["api_key_preview"].endswith("..."))

    def test_authentication_documents_every_rejection_code(self):
        doc = self.client.get(DOCS_URL).data

        codes = {row["code"] for row in doc["authentication"]["failure_codes"]}
        self.assertIn("invalid_api_key", codes)
        self.assertIn("account_suspended", codes)
        self.assertIn("account_not_active", codes)
        self.assertIn("no_credentials", codes)
        self.assertEqual(doc["authentication"]["primary"]["header"], "X-MIE-Api-Key")

    def test_dedup_checks_are_documented_in_execution_order(self):
        doc = self.client.get(DOCS_URL).data

        checks = doc["deduplication"]["checks"]
        self.assertEqual([check["order"] for check in checks], [1, 2, 3])
        self.assertEqual(
            [check["outcome"] for check in checks],
            [
                SubmissionStatus.PREVIOUSLY_REJECTED.value,
                SubmissionStatus.DUPLICATE_EXISTING.value,
                SubmissionStatus.DUPLICATE_IN_QUEUE.value,
            ],
        )

    def test_examples_use_the_host_the_caller_actually_reached(self):
        """Copy-pasteable examples must point at the environment the
        developer reached, not a hardcoded host."""

        doc = self.client.get(DOCS_URL).data

        host = "http://testserver"
        self.assertEqual(doc["api"]["base_url"], f"{host}/api/v1")
        self.assertIn(host, doc["quickstart"]["steps"][0]["curl"])
        self.assertTrue(
            all(endpoint["url"].startswith(host) for endpoint in doc["endpoints"])
        )

    def test_requires_credentials(self):
        self.client.credentials()

        self.assertEqual(
            self.client.get(DOCS_URL).status_code, status.HTTP_401_UNAUTHORIZED
        )


class DocumentationDownloadTests(DevSurfaceAuthBase):
    """The PDF is the same document in another medium."""

    def test_returns_a_pdf_attachment(self):
        response = self.client.get(DOCS_DOWNLOAD_URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment;", response["Content-Disposition"])
        self.assertIn(".pdf", response["Content-Disposition"])

    def test_body_is_a_real_pdf_with_content(self):
        content = self.client.get(DOCS_DOWNLOAD_URL).content

        self.assertTrue(content.startswith(b"%PDF-"))
        self.assertTrue(content.rstrip().endswith(b"%%EOF"))
        # A near-empty PDF would mean the renderer silently dropped the
        # document; the real one is tens of kilobytes across many pages.
        self.assertGreater(len(content), 20_000)

    def test_filename_is_derived_from_the_account(self):
        response = self.client.get(DOCS_DOWNLOAD_URL)

        handle = self.account.email.split("@")[0]
        self.assertIn(handle, response["Content-Disposition"])

    def test_renders_for_every_plan_type(self):
        for plan in MiePlanType.values:
            with self.subTest(plan=plan):
                account, key = make_approved_developer(plan_type=plan)
                self.client.credentials(HTTP_X_MIE_API_KEY=key)

                response = self.client.get(DOCS_DOWNLOAD_URL)

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertTrue(response.content.startswith(b"%PDF-"))
                self.assertEqual(account.status, "APPROVED")

    def test_requires_credentials(self):
        self.client.credentials()

        self.assertEqual(
            self.client.get(DOCS_DOWNLOAD_URL).status_code,
            status.HTTP_401_UNAUTHORIZED,
        )


class PlatformSessionAccessTests(DevSurfaceAuthBase):
    def test_queue_and_me_work_via_bearer_token_too(self):
        from api.mie.services import dev_token_service

        token = dev_token_service.issue_dev_token(self.account)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        me = self.client.get(ME_URL)
        queue = self.client.get(QUEUE_URL)

        self.assertEqual(me.status_code, status.HTTP_200_OK)
        self.assertEqual(queue.status_code, status.HTTP_200_OK)

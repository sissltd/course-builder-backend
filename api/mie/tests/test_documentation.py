"""Completeness guarantees for the generated developer documentation.

The point of generating the document from live constants is that it
cannot drift. These tests enforce the other half of that bargain: adding
an enum member, a status, or an event type without describing it must
fail here rather than shipping a silent gap in what developers read.

No database is touched - build_documentation only reads attributes off
the account - so these run as SimpleTestCase.
"""

import json
from datetime import datetime, timezone as dt_timezone
from types import SimpleNamespace

from django.conf import settings
from django.test import SimpleTestCase

from api.mie.enums import (
    DeveloperAccountStatus,
    MiePlanType,
    SubmissionStatus,
    WebhookDeliveryStatus,
    WebhookEventType,
)
from api.mie.services import documentation_pdf_service, documentation_service
from api.mie.services.reference import REFERENCE_SUFFIXES
from api.mie.services.submission_service import EVENT_TYPE_BY_STATUS


def _account(**overrides):
    """A stand-in DeveloperAccount - the service only reads attributes."""

    defaults = {
        "email": "dev@studio.io",
        "status": DeveloperAccountStatus.APPROVED,
        "plan_type": MiePlanType.PAID_PER_SUBMISSION,
        "webhook_url": "https://hooks.studio.io/mie",
        "api_key_prefix": "scb_live_a1b2c3",
        "api_key_issued_at": datetime(2026, 8, 21, 9, 0, tzinfo=dt_timezone.utc),
        "api_key_last_used_at": datetime(2026, 8, 30, 12, 0, tzinfo=dt_timezone.utc),
        "created_datetime": datetime(2026, 8, 20, 10, 0, tzinfo=dt_timezone.utc),
        "decided_at": datetime(2026, 8, 21, 9, 0, tzinfo=dt_timezone.utc),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class EnumCoverageTests(SimpleTestCase):
    """Every live enum member must be described somewhere in the document."""

    def test_every_plan_type_is_explained(self):
        for plan in MiePlanType:
            with self.subTest(plan=plan.value):
                doc = documentation_service.build_documentation(
                    _account(plan_type=plan)
                )
                self.assertTrue(doc["plan_and_payouts"]["what_it_means_for_you"])
                self.assertEqual(doc["plan_and_payouts"]["your_plan"], plan.value)

    def test_every_account_status_is_explained(self):
        for account_status in DeveloperAccountStatus:
            with self.subTest(status=account_status.value):
                doc = documentation_service.build_documentation(
                    _account(status=account_status)
                )
                self.assertTrue(doc["your_account"]["status_meaning"])

    def test_every_submission_status_is_documented(self):
        doc = documentation_service.build_documentation(_account())

        rows = doc["submission_lifecycle"]["statuses"]
        self.assertEqual(
            {row["status"] for row in rows}, set(SubmissionStatus.values)
        )
        for row in rows:
            with self.subTest(status=row["status"]):
                self.assertTrue(row["meaning"])
                self.assertTrue(row["set_by"])
                self.assertTrue(row["action"])
                self.assertIsInstance(row["terminal"], bool)
                self.assertEqual(
                    row["reference_suffix"],
                    REFERENCE_SUFFIXES[SubmissionStatus(row["status"])],
                )
                self.assertEqual(
                    row["webhook_event"],
                    EVENT_TYPE_BY_STATUS[SubmissionStatus(row["status"])].value,
                )

    def test_every_webhook_event_type_is_documented(self):
        doc = documentation_service.build_documentation(_account())

        events = doc["webhooks"]["events"]
        self.assertEqual(
            {event["type"] for event in events}, set(WebhookEventType.values)
        )
        for event in events:
            with self.subTest(event=event["type"]):
                self.assertTrue(event["fires_when"])
                self.assertTrue(event["sample_body"]["submission"]["reference"])

    def test_every_delivery_status_is_documented(self):
        doc = documentation_service.build_documentation(_account())

        documented = {
            row["status"] for row in doc["webhooks"]["retries"]["delivery_statuses"]
        }
        self.assertEqual(documented, set(WebhookDeliveryStatus.values))


class DerivedValueTests(SimpleTestCase):
    """Values must be read out of live code, not typed in by hand."""

    def setUp(self):
        self.doc = documentation_service.build_documentation(_account())

    def test_reference_examples_are_built_the_way_the_model_builds_them(self):
        for row in self.doc["reference_scheme"]["suffixes"]:
            with self.subTest(status=row["status"]):
                self.assertTrue(row["example"].startswith("SCB-"))
                self.assertTrue(row["example"].endswith(f"-{row['suffix']}"))

    def test_rate_limits_track_settings(self):
        rates = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
        rendered = " ".join(row["limit"] for row in self.doc["rate_limits"]["limits"])

        self.assertIn(rates["mie_ingest"].split("/")[0], rendered)
        self.assertIn(rates["mie_register"].split("/")[0], rendered)

    def test_page_size_tracks_settings(self):
        self.assertIn(
            str(settings.REST_FRAMEWORK["PAGE_SIZE"]),
            self.doc["pagination"]["query_parameters"]["size"],
        )

    def test_dedup_order_matches_the_engine(self):
        outcomes = [
            check["outcome"] for check in self.doc["deduplication"]["checks"]
        ]
        self.assertEqual(
            outcomes,
            [
                SubmissionStatus.PREVIOUSLY_REJECTED.value,
                SubmissionStatus.DUPLICATE_EXISTING.value,
                SubmissionStatus.DUPLICATE_IN_QUEUE.value,
            ],
        )


class ContentTests(SimpleTestCase):
    """The document has to actually answer a developer's questions."""

    def setUp(self):
        self.doc = documentation_service.build_documentation(_account())

    def test_is_json_serialisable(self):
        # It is returned straight through DRF; anything exotic breaks it.
        self.assertIsInstance(json.dumps(self.doc), str)

    def test_covers_registration_through_payout(self):
        stages = self.doc["integration_flow"]

        self.assertEqual(
            [stage["stage"] for stage in stages], list(range(1, len(stages) + 1))
        )
        names = " ".join(stage["name"] for stage in stages).lower()
        for expected in ("registration", "approval", "submission", "decision", "payout"):
            with self.subTest(stage=expected):
                self.assertIn(expected, names)
        for stage in stages:
            with self.subTest(stage=stage["name"]):
                self.assertTrue(stage["actor"])
                self.assertTrue(stage["what_happens"])
                self.assertTrue(stage["your_move"])

    def test_quickstart_curl_uses_the_real_auth_header(self):
        curls = [
            step["curl"] for step in self.doc["quickstart"]["steps"] if step["curl"]
        ]

        self.assertTrue(curls)
        for curl in curls:
            with self.subTest(curl=curl[:40]):
                self.assertIn("X-MIE-Api-Key", curl)

    def test_ships_runnable_signature_verification_in_two_languages(self):
        examples = self.doc["webhooks"]["verification"]["examples"]

        self.assertIn("hmac", examples["python"])
        self.assertIn("createHmac", examples["node"])
        for language in ("python", "node"):
            with self.subTest(language=language):
                self.assertIn("sha256", examples[language])

    def test_python_verification_sample_actually_verifies(self):
        """The snippet we hand developers must be correct, not decorative."""

        import hashlib
        import hmac
        import time

        from api.mie.services.webhook_dispatcher import sign_payload

        namespace: dict = {}
        exec(  # noqa: S102 - executing our own documented snippet is the test
            self.doc["webhooks"]["verification"]["examples"]["python"], namespace
        )

        secret = "test-signing-secret"
        body = b'{"event_id":"abc","type":"SUBMISSION_APPROVED"}'
        timestamp = str(int(time.time()))
        signature = sign_payload(secret, timestamp, body)

        self.assertTrue(namespace["verify"](body, timestamp, signature, secret))
        self.assertFalse(namespace["verify"](body, timestamp, "deadbeef", secret))
        self.assertFalse(namespace["verify"](b"tampered", timestamp, signature, secret))
        # And the replay window is enforced by the snippet itself.
        stale = str(int(time.time()) - 10_000)
        self.assertFalse(
            namespace["verify"](
                body, stale, sign_payload(secret, stale, body), secret
            )
        )
        # Sanity: our own signer agrees with the documented algorithm.
        self.assertEqual(
            signature,
            hmac.new(
                secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256
            ).hexdigest(),
        )

    def test_warns_that_the_reference_suffix_mutates(self):
        warning = self.doc["reference_scheme"]["critical_warning"].lower()

        self.assertIn("not stable", warning)
        self.assertIn("id", warning)

    def test_every_endpoint_entry_is_complete(self):
        for endpoint in self.doc["endpoints"]:
            with self.subTest(path=endpoint["path"]):
                for field in ("name", "method", "path", "url", "auth", "purpose"):
                    self.assertTrue(endpoint[field], msg=field)
                self.assertTrue(endpoint["errors"])
                self.assertTrue(
                    all("status" in error and "when" in error for error in endpoint["errors"])
                )

    def test_base_url_falls_back_to_a_placeholder_without_a_request(self):
        self.assertIn("<your-api-host>", self.doc["api"]["base_url"])


class PdfRenderingTests(SimpleTestCase):
    """The PDF must render the whole document, for any account shape."""

    def test_renders_a_valid_multi_page_pdf(self):
        account = _account()
        doc = documentation_service.build_documentation(account)

        pdf = documentation_pdf_service.build_documentation_pdf(doc, account=account)

        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertTrue(pdf.rstrip().endswith(b"%%EOF"))
        self.assertGreater(pdf.count(b"/Type /Page"), 10)

    def test_renders_for_an_account_with_no_key_material(self):
        """A PENDING account has null timestamps and no key prefix."""

        account = _account(
            status=DeveloperAccountStatus.PENDING,
            api_key_prefix="",
            api_key_issued_at=None,
            api_key_last_used_at=None,
            decided_at=None,
        )
        doc = documentation_service.build_documentation(account)

        pdf = documentation_pdf_service.build_documentation_pdf(doc, account=account)

        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertIsNone(doc["your_account"]["api_key_preview"])

    def test_every_section_reaches_the_pdf(self):
        account = _account()
        doc = documentation_service.build_documentation(account)

        self.assertEqual(
            set(doc), set(documentation_pdf_service.SECTION_TITLES),
            msg=(
                "A documentation section has no PDF title, so the download "
                "would silently omit it."
            ),
        )

    def test_filename_is_safe_for_any_email(self):
        for email, expected in (
            ("dev@studio.io", "mie-integration-reference-dev.pdf"),
            ("first.last+tag@studio.io", "mie-integration-reference-first-last-tag.pdf"),
        ):
            with self.subTest(email=email):
                name = documentation_pdf_service.filename_for(_account(email=email))
                self.assertEqual(name, expected)
                self.assertNotIn("/", name)
                self.assertNotIn('"', name)

    def test_humanise_respects_acronyms_and_human_labels(self):
        humanise = documentation_pdf_service._humanise

        self.assertEqual(humanise("api_base_url"), "API base URL")
        self.assertEqual(humanise("event_id"), "Event ID")
        self.assertEqual(humanise("webhook_url"), "Webhook URL")
        self.assertEqual(humanise("resulting_status"), "Resulting status")
        # Already-human labels pass through untouched.
        self.assertEqual(humanise("Account status"), "Account status")

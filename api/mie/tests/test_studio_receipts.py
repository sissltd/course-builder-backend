"""studio.receipt.v1: receipts recorded with each Studio delivery, and sent.

Recording is tested through the real endpoints with the exact request bytes,
since the payload hash is over the raw body. Sending patches httpx.Client.
"""

import hashlib
import hmac
import json
from datetime import timedelta
from unittest.mock import MagicMock, patch
from uuid import uuid4

import httpx
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from api.mie.enums import StudioReceiptStatus
from api.mie.models import CourseSubmission, StudioDeliveryReceipt
from api.mie.services import studio_receipt_sender
from api.mie.services.studio_review_service import content_hash
from api.mie.tests.factories import make_approved_developer
from api.mie.tests.test_studio_review_api import REVIEW, StudioReviewTestCase
from shared.services.storage_service import StorageService

INGEST = "/api/v1/mie/v1/submissions/"

SENDER = {
    "STUDIO_RECEIPT_URL": "https://studio.internal:3191/v1/course-builder/receipts",
    "STUDIO_RECEIPT_KEY_ID": "cb-demo-receipts-v1",
    "STUDIO_RECEIPT_SECRET": "s" * 48,
    "STUDIO_RECEIPT_CA_FILE": "/etc/studio/ca.pem",
}


class StudioReceiptTestCase(StudioReviewTestCase):
    def setUp(self):
        super().setUp()
        studio = override_settings(
            STUDIO_RECEIPT_DEVELOPER_ACCOUNT_ID=str(self.developer.pk),
            STUDIO_RECEIPT_ENVIRONMENT="demo",
        )
        studio.enable()
        self.addCleanup(studio.disable)

    def deliver(self, url, body, *, delivery=None, revision=None, key=None, **headers):
        """POST exactly these bytes with Studio's delivery headers."""

        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers.setdefault(
            "HTTP_X_STUDIO_PAYLOAD_SHA256", hashlib.sha256(raw).hexdigest()
        )
        if revision is not None:
            headers["HTTP_X_STUDIO_CONTENT_REVISION"] = str(revision)
        return self.client.post(
            url,
            data=raw,
            content_type="application/json",
            HTTP_X_MIE_API_KEY=key or self.key,
            HTTP_X_STUDIO_DELIVERY_ID=str(delivery or uuid4()),
            **headers,
        )

    def review(self, body=None, *, idem="delivery-1", **kwargs):
        body = dict(body or self.envelope)
        body["content_hash"] = content_hash(body["content"])
        kwargs.setdefault("revision", body["revision"])
        return self.deliver(REVIEW, body, HTTP_IDEMPOTENCY_KEY=idem, **kwargs)


class RecordingTests(StudioReceiptTestCase):
    def test_a_topic_receipt_carries_only_topic_fields(self):
        body = {"title": "Rust für Einsteiger: Ownership verstehen"}
        delivery = uuid4()

        response = self.deliver(INGEST, body, delivery=delivery)

        self.assertEqual(response.status_code, 201, response.data)
        receipt = StudioDeliveryReceipt.objects.get()
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.assertEqual(
            receipt.payload,
            {
                "contract_version": "studio.receipt.v1",
                "receipt_id": str(receipt.id),
                "delivery_id": str(delivery),
                "environment": "demo",
                "kind": "topic",
                "submission_id": response.data["id"],
                "payload_sha256": hashlib.sha256(raw).hexdigest(),
                "status": "received",
                "received_at": receipt.payload["received_at"],
            },
        )
        self.assertTrue(receipt.payload["received_at"].endswith("Z"))
        self.assertEqual(receipt.status, StudioReceiptStatus.PENDING)

    def test_a_topic_retry_replays_and_a_changed_body_is_a_conflict(self):
        delivery = uuid4()
        body = {"title": "Practical Observability for Small Teams"}
        first = self.deliver(INGEST, body, delivery=delivery)
        count = CourseSubmission.objects.count()

        again = self.deliver(INGEST, body, delivery=delivery)
        changed = self.deliver(INGEST, {"title": "Another idea"}, delivery=delivery)

        self.assertEqual(again.status_code, 201)
        self.assertEqual(again["Idempotent-Replayed"], "true")
        self.assertEqual(again.data["id"], first.data["id"])
        self.assertEqual(changed.status_code, 409)
        self.assertEqual(CourseSubmission.objects.count(), count)
        self.assertEqual(StudioDeliveryReceipt.objects.count(), 1)

    def test_a_payload_hash_that_does_not_match_the_bytes_stores_nothing(self):
        count = CourseSubmission.objects.count()

        response = self.deliver(
            INGEST, {"title": "Hash mismatch"}, HTTP_X_STUDIO_PAYLOAD_SHA256="0" * 64
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(CourseSubmission.objects.count(), count)
        self.assertFalse(StudioDeliveryReceipt.objects.exists())

    def test_other_callers_and_an_unset_account_record_nothing(self):
        _, other_key = make_approved_developer()
        self.assertEqual(
            self.deliver(
                INGEST, {"title": "Someone else's idea"}, key=other_key
            ).status_code,
            201,
        )
        with override_settings(STUDIO_RECEIPT_DEVELOPER_ACCOUNT_ID=""):
            self.assertEqual(
                self.deliver(INGEST, {"title": "Before activation"}).status_code, 201
            )
        self.assertFalse(StudioDeliveryReceipt.objects.exists())

    def test_a_text_receipt_counts_the_stored_course(self):
        delivery = uuid4()

        response = self.review(delivery=delivery)

        self.assertEqual(response.status_code, 200, response.data)
        payload = StudioDeliveryReceipt.objects.get().payload
        self.assertEqual(payload["kind"], "text")
        self.assertEqual(payload["course_id"], str(self.idea.reserved_course_id))
        self.assertEqual(payload["revision"], 1)
        self.assertEqual((payload["module_count"], payload["lesson_count"]), (4, 12))
        self.assertNotIn("media_persisted", payload)

    def test_the_revision_header_must_be_sent_and_match(self):
        for revision in ("", "2"):
            response = self.review(revision=revision)
            self.assertEqual(response.status_code, 400, revision)
        self.assertFalse(StudioDeliveryReceipt.objects.exists())
        self.assertFalse(self.idea.studio_receipts.exists())

    def test_a_refused_review_records_no_receipt(self):
        body = dict(self.envelope)
        body["content"] = {**self.content, "final_assessment": {"questions": []}}

        self.assertEqual(self.review(body).status_code, 400)
        self.assertFalse(StudioDeliveryReceipt.objects.exists())

    def test_an_idempotent_replay_records_no_second_receipt(self):
        delivery = uuid4()
        first = self.review(delivery=delivery)
        again = self.review(delivery=delivery)

        self.assertEqual(again.data, first.data)
        self.assertEqual(StudioDeliveryReceipt.objects.count(), 1)

    def test_a_video_receipt_is_recorded_only_once_media_is_verified(self):
        self._approve()
        preview = self._upload("COURSE_PREVIEW_VIDEO", "preview.mp4")
        video = self._upload("LESSON_VIDEO", "lesson.mp4")
        captions = self._upload("SUBTITLE", "captions.srt")
        body = {
            **self.envelope,
            "kind": "video",
            "content": {
                "preview_video_url": preview,
                "lessons": [
                    {
                        "module_order": mi,
                        "lesson_order": li,
                        "video_url": video,
                        "captions_url": captions,
                        "transcript": f"Narration {mi}-{li}",
                    }
                    for mi in range(1, 5)
                    for li in range(1, 4)
                ],
            },
        }
        with patch.object(StorageService, "verify_file", return_value=False):
            self.assertEqual(self.review(body, idem="video-1").status_code, 400)
        self.assertFalse(StudioDeliveryReceipt.objects.filter(kind="video").exists())

        with patch.object(StorageService, "verify_file", return_value=True):
            self.assertEqual(self.review(body, idem="video-1").status_code, 200)

        payload = StudioDeliveryReceipt.objects.get(kind="video").payload
        self.assertIs(payload["media_persisted"], True)
        self.assertEqual((payload["module_count"], payload["lesson_count"]), (4, 12))


def _response(status, *, body=None, headers=None):
    return httpx.Response(
        status,
        json=body if body is not None else {},
        headers=headers,
        request=httpx.Request("POST", SENDER["STUDIO_RECEIPT_URL"]),
    )


@override_settings(**SENDER)
class SendingTests(StudioReceiptTestCase):
    def setUp(self):
        super().setUp()
        self.deliver(INGEST, {"title": "A topic to acknowledge"})
        self.receipt = StudioDeliveryReceipt.objects.get()

    def send(self, *responses):
        """Run one pass per response; returns the mocked client."""

        client = MagicMock()
        client.post.side_effect = list(responses)
        with patch.object(studio_receipt_sender.httpx, "Client") as factory:
            factory.return_value.__enter__.return_value = client
            for _ in responses:
                StudioDeliveryReceipt.objects.update(next_retry_at=None)
                studio_receipt_sender.send_due_receipts()
        self.factory_kwargs = factory.call_args.kwargs
        self.receipt.refresh_from_db()
        return client

    def test_a_signed_receipt_is_delivered_over_verified_tls(self):
        accepted = {
            "recorded": True,
            "receipt_id": str(self.receipt.id),
            "reconciliation": "pending",
        }

        client = self.send(_response(202, body=accepted))

        call = client.post.call_args
        body, headers = call.kwargs["content"], call.kwargs["headers"]
        self.assertEqual(json.loads(body), self.receipt.payload)
        self.assertEqual(headers["X-Receipt-Key-Id"], "cb-demo-receipts-v1")
        expected = hmac.new(
            SENDER["STUDIO_RECEIPT_SECRET"].encode(),
            headers["X-MIE-Timestamp"].encode() + b"." + body,
            hashlib.sha256,
        ).hexdigest()
        self.assertEqual(headers["X-MIE-Signature"], expected)
        self.assertEqual(self.factory_kwargs["verify"], "/etc/studio/ca.pem")
        self.assertEqual(self.receipt.status, StudioReceiptStatus.DELIVERED)
        self.assertEqual(self.receipt.response, accepted)
        self.assertNotIn(
            SENDER["STUDIO_RECEIPT_SECRET"], json.dumps(self.receipt.payload)
        )

    def test_a_duplicate_answer_is_delivered_too(self):
        self.send(_response(200, body={"recorded": True, "duplicate": True}))

        self.assertEqual(self.receipt.status, StudioReceiptStatus.DELIVERED)

    def test_retries_resend_the_same_body_with_a_fresh_signature(self):
        with patch.object(studio_receipt_sender.time, "time", side_effect=[1000, 2000]):
            client = self.send(_response(503), _response(202))

        first, second = (call.kwargs for call in client.post.call_args_list)
        self.assertEqual(first["content"], second["content"])
        self.assertNotEqual(
            first["headers"]["X-MIE-Signature"], second["headers"]["X-MIE-Signature"]
        )
        self.assertEqual(self.receipt.status, StudioReceiptStatus.DELIVERED)

    def test_a_retryable_failure_waits_for_the_schedule_or_retry_after(self):
        before = timezone.now()
        self.send(_response(503))
        self.assertEqual(self.receipt.status, StudioReceiptStatus.PENDING)
        self.assertAlmostEqual(
            (self.receipt.next_retry_at - before).total_seconds(), 60, delta=5
        )

        before = timezone.now()
        client = MagicMock()
        client.post.return_value = _response(429, headers={"Retry-After": "600"})
        with patch.object(studio_receipt_sender.httpx, "Client") as factory:
            factory.return_value.__enter__.return_value = client
            StudioDeliveryReceipt.objects.update(next_retry_at=None)
            studio_receipt_sender.send_due_receipts()
        self.receipt.refresh_from_db()
        self.assertAlmostEqual(
            (self.receipt.next_retry_at - before).total_seconds(), 600, delta=5
        )

    def test_a_timeout_is_retried(self):
        self.send(httpx.ConnectTimeout("timed out"))

        self.assertEqual(self.receipt.status, StudioReceiptStatus.PENDING)
        self.assertEqual(self.receipt.attempts, 1)
        self.assertIn("timed out", self.receipt.last_error)

    def test_five_failures_hold_the_receipt_and_alert(self):
        with self.assertLogs("api.mie.services.studio_receipt_sender", "ERROR"):
            self.send(*[_response(503) for _ in range(5)])

        self.assertEqual(self.receipt.status, StudioReceiptStatus.HELD)
        self.assertEqual(self.receipt.attempts, 5)

    def test_a_refusal_holds_the_receipt_at_once(self):
        for code in (400, 401, 403, 404, 409, 413):
            StudioDeliveryReceipt.objects.update(
                status=StudioReceiptStatus.PENDING, attempts=0
            )
            with self.assertLogs("api.mie.services.studio_receipt_sender", "ERROR"):
                self.send(_response(code))
            self.assertEqual(self.receipt.status, StudioReceiptStatus.HELD, code)
            self.assertEqual(self.receipt.last_response_code, code)

    def test_nothing_is_sent_until_the_receiver_is_configured(self):
        with (
            override_settings(STUDIO_RECEIPT_CA_FILE=""),
            patch.object(studio_receipt_sender.httpx, "Client") as factory,
        ):
            studio_receipt_sender.send_due_receipts()

        factory.assert_not_called()

    def test_requeue_keeps_the_receipt_id_and_payload(self):
        payload = dict(self.receipt.payload)
        StudioDeliveryReceipt.objects.update(
            status=StudioReceiptStatus.HELD,
            attempts=5,
            next_retry_at=timezone.now() + timedelta(hours=1),
        )

        call_command(
            "requeue_studio_receipts", str(self.receipt.id), stdout=MagicMock()
        )

        self.receipt.refresh_from_db()
        self.assertEqual(self.receipt.status, StudioReceiptStatus.PENDING)
        self.assertEqual(self.receipt.attempts, 0)
        self.assertEqual(self.receipt.payload, payload)

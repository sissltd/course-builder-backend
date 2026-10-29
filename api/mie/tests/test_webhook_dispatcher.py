import hashlib
import hmac
import json
from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from api.mie.enums import DeveloperAccountStatus, WebhookDeliveryStatus
from api.mie.models import WebhookEvent
from api.mie.services.submission_service import EVENT_TYPE_BY_STATUS
from api.mie.services.webhook_dispatcher import (
    MAX_ATTEMPTS,
    RETRY_DELAYS_SECONDS,
    dispatch_due_events,
    drop_events_for_rejected_account,
    due_events,
    render_body,
    sign_payload,
)
from api.mie.tests.factories import (
    make_approved_developer,
    make_decided_submission,
    make_submission,
    make_webhook_event,
)


def _ok(event_id):
    return (True, None, "")


def _fail(event_id, code=500):
    return (False, code, f"HTTP {code}")


class SigningTests(TestCase):
    def test_signature_matches_reference_implementation(self):
        secret = "topsecret"
        timestamp = "1700000000"
        body = b'{"a":1}'

        expected = hmac.new(
            b"topsecret", b"1700000000." + body, hashlib.sha256
        ).hexdigest()

        self.assertEqual(sign_payload(secret, timestamp, body), expected)

    def test_render_body_is_stable_and_carries_event_identity(self):
        event = make_webhook_event()

        first = render_body(event)
        second = render_body(event)

        self.assertEqual(first, second)
        parsed = json.loads(first)
        self.assertEqual(parsed["event_id"], str(event.id))
        self.assertEqual(parsed["type"], event.event_type)
        self.assertIn("reference", parsed["submission"])


class DueEventSelectionTests(TestCase):
    def test_never_attempted_and_due_retries_are_selected(self):
        fresh = make_webhook_event()  # next_retry_at null
        due = make_webhook_event(next_retry_at=timezone.now() - timedelta(minutes=1))
        make_webhook_event(next_retry_at=timezone.now() + timedelta(hours=1))
        make_webhook_event(delivery_status=WebhookDeliveryStatus.DELIVERED)
        make_webhook_event(delivery_status=WebhookDeliveryStatus.FAILED)

        selected = set(due_events().values_list("id", flat=True))

        self.assertEqual(selected, {fresh.id, due.id})

    def test_selection_is_one_query_with_no_per_row_lookups(self):
        for _ in range(5):
            make_webhook_event()

        with self.assertNumQueries(1):
            list(due_events())


class DispatchPassTests(TestCase):
    def setUp(self):
        self.account, _raw = make_approved_developer()
        self.submission = make_submission(developer=self.account)
        self.event = make_webhook_event(submission=self.submission)

    def test_successful_delivery_marks_delivered_with_timestamp(self):
        with mock.patch(
            "api.mie.services.webhook_dispatcher._post_once",
            return_value=_ok(self.event.id),
        ):
            report = dispatch_due_events()

        self.event.refresh_from_db()
        self.assertEqual(report.delivered, 1)
        self.assertEqual(self.event.delivery_status, WebhookDeliveryStatus.DELIVERED)
        self.assertIsNotNone(self.event.delivered_at)
        self.assertIsNone(self.event.next_retry_at)
        self.assertEqual(self.event.last_response_code, None)

    def test_failure_schedules_backoff_and_increments_attempts(self):
        with mock.patch(
            "api.mie.services.webhook_dispatcher._post_once",
            return_value=_fail(self.event.id),
        ):
            report = dispatch_due_events()

        self.event.refresh_from_db()
        self.assertEqual(report.retried, 1)
        self.assertEqual(self.event.attempts, 1)
        self.assertEqual(self.event.delivery_status, WebhookDeliveryStatus.PENDING)
        delay = self.event.next_retry_at - timezone.now()
        # ~RETRY_DELAYS_SECONDS[0]; allow the microseconds the pass took.
        self.assertGreaterEqual(delay, timedelta(seconds=RETRY_DELAYS_SECONDS[0] - 2))
        self.assertLessEqual(delay, timedelta(seconds=RETRY_DELAYS_SECONDS[0]))

    def test_exhausted_attempts_fail_terminally(self):
        self.event.attempts = MAX_ATTEMPTS - 1
        self.event.save(update_fields=["attempts"])

        with mock.patch(
            "api.mie.services.webhook_dispatcher._post_once",
            return_value=_fail(self.event.id),
        ):
            report = dispatch_due_events()

        self.event.refresh_from_db()
        self.assertEqual(report.failed, 1)
        self.assertEqual(self.event.delivery_status, WebhookDeliveryStatus.FAILED)
        self.assertIsNone(self.event.next_retry_at)

    def test_transport_exception_counts_as_failure_not_crash(self):
        with mock.patch(
            "api.mie.services.webhook_dispatcher._post_once",
            return_value=(False, None, "connect timeout"),
        ):
            report = dispatch_due_events()

        self.assertEqual(report.retried, 1)
        self.event.refresh_from_db()
        self.assertEqual(self.event.last_error, "connect timeout")

    def test_empty_queue_costs_one_indexed_query_and_no_http(self):
        WebhookEvent.objects.all().delete()  # drop setUp's event

        with mock.patch("api.mie.services.webhook_dispatcher._post_once") as post:
            with self.assertNumQueries(1):  # the due-events SELECT only
                report = dispatch_due_events()

        post.assert_not_called()
        self.assertEqual(report.delivered, 0)

    def test_many_events_batch_through_bulk_update(self):
        extra = []
        for i in range(9):
            submission = make_submission(developer=self.account, title=f"Idea {i}")
            extra.append(make_webhook_event(submission=submission))

        with mock.patch(
            "api.mie.services.webhook_dispatcher._post_once", return_value=_ok(None)
        ) as post:
            with self.assertNumQueries(3):  # select due + select ids + one bulk update
                report = dispatch_due_events()

        self.assertEqual(report.delivered, 10)
        self.assertEqual(post.call_count, 10)


class AccountStatePartitioningTests(TestCase):
    def setUp(self):
        self.account, _raw = make_approved_developer()

    def test_suspended_account_events_are_frozen_not_failed(self):
        self.account.status = DeveloperAccountStatus.SUSPENDED
        self.account.save(update_fields=["status", "updated_datetime"])
        event = make_webhook_event(submission=make_submission(developer=self.account))

        with mock.patch("api.mie.services.webhook_dispatcher._post_once") as post:
            report = dispatch_due_events()

        self.assertEqual(report.delivered, 0)
        post.assert_not_called()
        event.refresh_from_db()
        self.assertEqual(event.delivery_status, WebhookDeliveryStatus.PENDING)
        self.assertIsNone(event.next_retry_at)  # still picked up on revival

    def test_rejected_account_events_fail_immediately_via_partition(self):
        decided = make_decided_submission(developer=self.account, approved=False)
        event = make_webhook_event(
            submission=decided, event_type=EVENT_TYPE_BY_STATUS[decided.status]
        )
        # Mirror the real rejection path: credentials are wiped before the
        # status flips (the DB constraint requires exactly this order).
        self.account.api_key_prefix = ""
        self.account.api_key_hash = ""
        self.account.signing_secret = ""
        self.account.status = DeveloperAccountStatus.REJECTED
        self.account.save()

        with mock.patch("api.mie.services.webhook_dispatcher._post_once") as post:
            report = dispatch_due_events()

        self.assertEqual(report.failed, 1)
        post.assert_not_called()
        event.refresh_from_db()
        self.assertEqual(event.delivery_status, WebhookDeliveryStatus.FAILED)
        self.assertIn("rejected", event.last_error)


class RejectionHookTests(TestCase):
    def test_drop_events_for_rejected_account_only_touches_pending(self):
        account, _raw = make_approved_developer()
        submission = make_submission(developer=account)
        pending = make_webhook_event(submission=submission)
        delivered = make_webhook_event(
            submission=submission, delivery_status=WebhookDeliveryStatus.DELIVERED
        )

        dropped = drop_events_for_rejected_account(account)

        self.assertEqual(dropped, 1)
        pending.refresh_from_db()
        delivered.refresh_from_db()
        self.assertEqual(pending.delivery_status, WebhookDeliveryStatus.FAILED)
        self.assertEqual(delivered.delivery_status, WebhookDeliveryStatus.DELIVERED)

    def test_reject_service_drops_pending_events(self):
        from api.courses.tests.factories import make_user
        from api.mie.enums import SubmissionStatus
        from api.mie.services import developer_service

        admin = make_user(role="SUPER_ADMIN")
        account, _raw = make_approved_developer()
        submission = make_decided_submission(approved=True, developer=account)
        submission.status = SubmissionStatus.REJECTED
        submission.save(update_fields=["status"])
        event = make_webhook_event(submission=submission)

        developer_service.reject_developer(actor=admin, account=account)

        event.refresh_from_db()
        self.assertEqual(event.delivery_status, WebhookDeliveryStatus.FAILED)


class SignatureWireConsistencyTests(TestCase):
    def test_signed_bytes_equal_sent_body(self):
        """The signature must verify over exactly the bytes on the wire."""

        account, _raw = make_approved_developer()
        event = make_webhook_event(submission=make_submission(developer=account))
        captured = {}

        def fake_post(url, content, headers):
            captured["body"] = content
            captured["headers"] = headers
            return _ok(event.id)

        from api.mie.services import webhook_dispatcher

        prepared = webhook_dispatcher._prepare_request(event)
        url, body, headers = prepared
        # Simulate the receiver recomputing:
        timestamp = headers["X-MIE-Timestamp"]
        recomputed = sign_payload(account.signing_secret, timestamp, body)

        self.assertEqual(recomputed, headers["X-MIE-Signature"])
        self.assertIsInstance(body, bytes)

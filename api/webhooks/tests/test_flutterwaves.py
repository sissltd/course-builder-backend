"""Tests for the Flutterwave webhook pipeline: view, service, and celery task.

The view acknowledges, dedupes, and persists raw events; the task calls the
service; the service reconciles the transfer outbox. Each layer is tested
against its own contract, with the boundaries between them mocked.
"""

import base64
import hashlib
import hmac
import json
import uuid
from decimal import Decimal
from unittest.mock import patch

from celery.exceptions import Retry
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITransactionTestCase

from api.courses.tests.factories import make_user
from api.payments.models import BankAccount
from api.payments.models.ledgeraccount_models import InternalAccount
from api.payments.models.transaction_model import Transaction
from api.platform.enums import PaymentProcessors
from api.wallet.enums import (
    TransactionStatus,
    TransactionType,
    WithdrawalRequestStatus,
)
from api.wallet.models import Wallet, WithdrawalRequest
from api.webhooks.services.flutterwave_webhook_services import (
    FlutterwaveWebhookServices,
    NonRetryableWebhookError,
)
from api.webhooks.tasks import process_webhook_task
from api.webhooks.views.flutterwave_webhook_views import FlutterwaveWebhookView
from core.models import TransferOutboxEvent, WebhookEvent
from shared.utils.encryption import encrypt_field

WEBHOOK_SECRET = "test-flutterwave-secret"


def _sign(payload_bytes, secret=WEBHOOK_SECRET):
    """Compute the base64 HMAC-SHA256 signature Flutterwave would send."""
    digest = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


class FlutterwaveWebhookViewTests(APITransactionTestCase):
    """APITransactionTestCase so transaction.on_commit actually fires - the
    view enqueues the celery task there, and a wrapping TestCase transaction
    would never commit, so the enqueue could never be observed."""

    url = reverse("webhook:flutterwave-webhook")

    def setUp(self):
        secret_patcher = patch.object(
            FlutterwaveWebhookView, "FLUTTERWAVE_SECRET_HASH", WEBHOOK_SECRET
        )
        enqueue_patcher = patch(
            "api.webhooks.views.flutterwave_webhook_views.process_webhook_task.delay"
        )
        secret_patcher.start()
        self.enqueue_task = enqueue_patcher.start()
        self.addCleanup(secret_patcher.stop)
        self.addCleanup(enqueue_patcher.stop)

    def valid_payload(self):
        return {
            "webhook_id": "flw-event-123",
            "type": "transfer.disburse",
            "data": {
                "id": 987654,
                "reference": "TRF-REF-001",
                "amount": 100000,  # kobo
                "status": "SUCCESSFUL",
            },
        }

    def post_webhook(self, payload=None, sign=True, raw_body=None):
        if raw_body is not None:
            body = raw_body
        else:
            body = json.dumps(payload if payload is not None else self.valid_payload())
        extra = {}
        if sign:
            extra["HTTP_FLUTTERWAVE_SIGNATURE"] = _sign(body.encode("utf-8"))
        return self.client.post(
            self.url, data=body, content_type="application/json", **extra
        )

    def test_valid_webhook_persists_event_and_enqueues_processing(self):
        payload = self.valid_payload()

        response = self.post_webhook(payload)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json(), {"status": "accepted"})
        event = WebhookEvent.objects.get(event_id="flw-event-123")
        self.assertEqual(event.event_type, "transfer.disburse")
        self.assertEqual(event.payload, payload)
        self.assertEqual(event.status, "PENDING")
        self.assertEqual(event.provider, PaymentProcessors.FLUTTERWAVE)
        self.assertEqual(event.amount, Decimal("1000.00"))  # kobo -> naira
        self.enqueue_task.assert_called_once_with(event.id)

    def test_duplicate_event_is_acknowledged_without_requeueing(self):
        self.post_webhook()

        response = self.post_webhook()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(WebhookEvent.objects.count(), 1)
        self.enqueue_task.assert_called_once()

    def test_failed_event_is_requeued_on_redelivery(self):
        """A redelivered event whose first processing attempt FAILED is reset
        to PENDING and re-queued so a transient failure isn't stranded."""
        event = WebhookEvent.objects.create(
            event_id="flw-event-123",
            event_type="transfer.disburse",
            payload=self.valid_payload(),
            status="FAILED",
            provider=PaymentProcessors.FLUTTERWAVE,
        )

        response = self.post_webhook()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        event.refresh_from_db()
        self.assertEqual(event.status, "PENDING")
        self.enqueue_task.assert_called_once_with(event.id)

    def test_missing_signature_is_rejected_outside_production(self):
        with patch(
            "api.webhooks.views.flutterwave_webhook_views.DJANGO_ENV", "development"
        ):
            response = self.post_webhook(sign=False)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.json(), {"error": "Missing signature"})
        self.assertEqual(WebhookEvent.objects.count(), 0)
        self.enqueue_task.assert_not_called()

    def test_missing_signature_is_acknowledged_in_production(self):
        """In production a bad delivery still gets a 200 so the processor
        doesn't keep retrying a request that can never validate."""
        with patch(
            "api.webhooks.views.flutterwave_webhook_views.DJANGO_ENV", "production"
        ):
            response = self.post_webhook(sign=False)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["message"], "Invalid signature.")
        self.assertEqual(WebhookEvent.objects.count(), 0)

    def test_forged_signature_is_acknowledged_without_processing(self):
        response = self.client.post(
            self.url,
            data=json.dumps(self.valid_payload()),
            content_type="application/json",
            HTTP_FLUTTERWAVE_SIGNATURE="forged-signature",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()["message"], "Signature failed.")
        self.assertEqual(WebhookEvent.objects.count(), 0)
        self.enqueue_task.assert_not_called()

    def test_malformed_json_returns_400(self):
        body = "not-json"

        response = self.post_webhook(raw_body=body)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(WebhookEvent.objects.count(), 0)

    def test_payload_without_any_event_id_returns_400(self):
        payload = self.valid_payload()
        del payload["webhook_id"]

        response = self.post_webhook(payload)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(WebhookEvent.objects.count(), 0)


    def test_unconfigured_secret_fails_closed_with_503(self):
        with patch.object(FlutterwaveWebhookView, "FLUTTERWAVE_SECRET_HASH", ""):
            response = self.post_webhook()

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(WebhookEvent.objects.count(), 0)
        self.enqueue_task.assert_not_called()

    def test_nested_amount_shape_is_converted_to_naira(self):
        payload = self.valid_payload()
        payload["data"]["amount"] = {"value": 5000, "currency": "NGN"}

        response = self.post_webhook(payload)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        event = WebhookEvent.objects.get(event_id="flw-event-123")
        self.assertEqual(event.amount, Decimal("50.00"))

    def test_unparseable_amount_is_stored_as_none(self):
        payload = self.valid_payload()
        payload["data"]["amount"] = "not-a-number"

        response = self.post_webhook(payload)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        event = WebhookEvent.objects.get(event_id="flw-event-123")
        self.assertIsNone(event.amount)


class VerifyRequestSignatureTests(TestCase):
    def test_valid_signature_matches(self):
        payload = b'{"type": "transfer.disburse"}'

        self.assertTrue(
            FlutterwaveWebhookServices.verify_request_signature(
                payload=payload, signature=_sign(payload), secret_key=WEBHOOK_SECRET
            )
        )

    def test_signature_computed_with_wrong_secret_does_not_match(self):
        payload = b'{"type": "transfer.disburse"}'

        self.assertFalse(
            FlutterwaveWebhookServices.verify_request_signature(
                payload=payload,
                signature=_sign(payload, secret="a-different-secret"),
                secret_key=WEBHOOK_SECRET,
            )
        )

    def test_tampered_payload_does_not_match(self):
        payload = b'{"type": "transfer.disburse"}'
        signature = _sign(payload)

        self.assertFalse(
            FlutterwaveWebhookServices.verify_request_signature(
                payload=payload + b" ",  # one byte different
                signature=signature,
                secret_key=WEBHOOK_SECRET,
            )
        )


class ParseWebhookEventRoutingTests(TestCase):
    """parse_webhook_event only routes; the handlers themselves are covered
    by the Handle* test classes below."""

    @patch.object(FlutterwaveWebhookServices, "_handle_transfer_failure")
    @patch.object(FlutterwaveWebhookServices, "_handle_transfer_success")
    def test_successful_disburse_routes_to_success_handler(self, success, failure):
        data = {"reference": "TRF-1", "status": "SUCCESSFUL"}

        FlutterwaveWebhookServices.parse_webhook_event(
            {"type": "transfer.disburse", "data": data}
        )

        success.assert_called_once_with(data)
        failure.assert_not_called()

    @patch.object(FlutterwaveWebhookServices, "_handle_transfer_failure")
    @patch.object(FlutterwaveWebhookServices, "_handle_transfer_success")
    def test_non_successful_disburse_routes_to_failure_handler(self, success, failure):
        data = {"reference": "TRF-1", "status": "FAILED"}

        FlutterwaveWebhookServices.parse_webhook_event(
            {"type": "transfer.disburse", "data": data}
        )

        failure.assert_called_once_with(data)
        success.assert_not_called()

    @patch.object(FlutterwaveWebhookServices, "_handle_transfer_failure")
    @patch.object(FlutterwaveWebhookServices, "_handle_transfer_success")
    def test_failed_and_reversed_event_types_route_to_failure_handler(
        self, success, failure
    ):
        for event_type in (
            "transfer.failed",
            "transfer.failure",
            "transfer.reversed",
            "transfer.reversal",
        ):
            with self.subTest(event_type=event_type):
                data = {"reference": "TRF-1", "status": "FAILED"}

                FlutterwaveWebhookServices.parse_webhook_event(
                    {"type": event_type, "data": data}
                )

                failure.assert_called_with(data)
                success.assert_not_called()
                failure.reset_mock()

    def test_unhandled_event_type_raises_non_retryable(self):
        with self.assertRaises(NonRetryableWebhookError):
            FlutterwaveWebhookServices.parse_webhook_event(
                {"type": "charge.success", "data": {}}
            )

    def test_missing_event_type_raises_non_retryable(self):
        with self.assertRaises(NonRetryableWebhookError):
            FlutterwaveWebhookServices.parse_webhook_event({"data": {}})

    def test_non_dict_payload_raises_non_retryable(self):
        with self.assertRaises(NonRetryableWebhookError):
            FlutterwaveWebhookServices.parse_webhook_event(["not", "a", "dict"])


class WebhookAmountExtractionTests(TestCase):
    def test_flat_kobo_amount_is_converted_to_naira(self):
        self.assertEqual(
            FlutterwaveWebhookServices._webhook_amount({"amount": 100000}),
            Decimal("1000.00"),
        )

    def test_nested_amount_value_is_converted(self):
        self.assertEqual(
            FlutterwaveWebhookServices._webhook_amount(
                {"amount": {"value": 5000, "currency": "NGN"}}
            ),
            Decimal("50.00"),
        )

    def test_garbage_amount_returns_none(self):
        self.assertIsNone(
            FlutterwaveWebhookServices._webhook_amount({"amount": "not-a-number"})
        )

    def test_missing_amount_returns_none(self):
        self.assertIsNone(FlutterwaveWebhookServices._webhook_amount({}))


class HandleTransferSuccessTests(TestCase):
    def setUp(self):
        log_activity_patcher = patch(
            "api.webhooks.services.flutterwave_webhook_services.log_activity"
        )
        email_patcher = patch(
            "api.webhooks.services.flutterwave_webhook_services"
            ".Notification.emit_email_notification"
        )
        self.log_activity = log_activity_patcher.start()
        self.send_email = email_patcher.start()
        self.addCleanup(log_activity_patcher.stop)
        self.addCleanup(email_patcher.stop)

        self.user = make_user()
        self.wallet = Wallet.objects.create(user=self.user)
        self.entry = TransferOutboxEvent.objects.create(
            reference="TRF-SUCCESS-1",
            user=self.user,
            amount=Decimal("1000.00"),
            transfer_processor=PaymentProcessors.FLUTTERWAVE,
            status=TransferOutboxEvent.Status.SUBMITTED,
            wallet=self.wallet,
        )
        # The pending debit the withdrawal flow created when the user
        # confirmed; the webhook settles it.
        self.debit_txn = Transaction.objects.create(
            wallet=self.wallet,
            reference=self.entry.reference,
            amount=self.entry.amount,
            type=TransactionType.DEBIT,
            status=TransactionStatus.PENDING,
        )

    def webhook_data(self, **overrides):
        data = {
            "reference": self.entry.reference,
            "status": "SUCCESSFUL",
            "amount": int(self.entry.amount * 100),
            "meta": {},
        }
        data.update(overrides)
        return data

    def test_success_marks_entry_processed_and_settles_ledgers(self):
        FlutterwaveWebhookServices.parse_webhook_event(
            {"type": "transfer.disburse", "data": self.webhook_data()}
        )

        self.entry.refresh_from_db()
        self.assertEqual(self.entry.status, TransferOutboxEvent.Status.PROCESSED)

        # Money moves from the suspense transit account to the Flutterwave
        # settlement account, as a balanced debit/credit pair.
        suspense = InternalAccount.objects.get(code_name="suspense")
        flutterwave = InternalAccount.objects.get(code_name="flutterwave_transfer")
        self.assertEqual(suspense.balance, Decimal("-1000.00"))
        self.assertEqual(flutterwave.balance, Decimal("1000.00"))

        self.debit_txn.refresh_from_db()
        self.assertEqual(self.debit_txn.status, TransactionStatus.COMPLETED)

        self.log_activity.assert_called_once()
        self.send_email.assert_called_once()
        self.assertEqual(
            self.send_email.call_args.kwargs["receivers"], [self.user.email]
        )

    def test_success_for_non_submitted_entry_is_a_noop(self):
        """Duplicate or out-of-order delivery: only a SUBMITTED entry may be
        settled, anything else must not move money twice."""
        self.entry.status = TransferOutboxEvent.Status.PROCESSED
        self.entry.save()

        FlutterwaveWebhookServices.parse_webhook_event(
            {"type": "transfer.disburse", "data": self.webhook_data()}
        )

        # Only the fixture debit exists - no internal transfer was created.
        self.assertEqual(
            Transaction.objects.filter(reference=self.entry.reference).count(), 1
        )
        self.debit_txn.refresh_from_db()
        self.assertEqual(self.debit_txn.status, TransactionStatus.PENDING)
        self.log_activity.assert_not_called()
        self.send_email.assert_not_called()

    def test_success_with_unknown_reference_raises_non_retryable(self):
        with self.assertRaises(NonRetryableWebhookError):
            FlutterwaveWebhookServices.parse_webhook_event(
                {
                    "type": "transfer.disburse",
                    "data": self.webhook_data(reference="TRF-UNKNOWN"),
                }
            )

    def test_success_with_mismatched_webhook_amount_uses_outbox_amount(self):
        """The outbox amount is authoritative; a tampered payload amount is
        logged but must not change how much money moves."""
        FlutterwaveWebhookServices.parse_webhook_event(
            {"type": "transfer.disburse", "data": self.webhook_data(amount=100)}
        )

        flutterwave = InternalAccount.objects.get(code_name="flutterwave_transfer")
        self.assertEqual(flutterwave.balance, Decimal("1000.00"))
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.status, TransferOutboxEvent.Status.PROCESSED)


class HandleTransferFailureTests(TestCase):
    def setUp(self):
        log_activity_patcher = patch(
            "api.webhooks.services.flutterwave_webhook_services.log_activity"
        )
        email_patcher = patch(
            "api.webhooks.services.flutterwave_webhook_services"
            ".Notification.emit_email_notification"
        )
        self.log_activity = log_activity_patcher.start()
        self.send_email = email_patcher.start()
        self.addCleanup(log_activity_patcher.stop)
        self.addCleanup(email_patcher.stop)

        self.user = make_user()
        self.wallet = Wallet.objects.create(user=self.user)
        payout_account = BankAccount.objects.create(
            user=self.user,
            account_type="LOCAL",
            bank_name="Access Bank",
            account_number=encrypt_field("1234567890"),
            account_name="Test User",
            bank_code="058",
            is_default=True,
        )
        self.withdrawal_request = WithdrawalRequest.objects.create(
            user=self.user,
            wallet=self.wallet,
            payout_account=payout_account,
            amount=Decimal("1000.00"),
            status=WithdrawalRequestStatus.CONFIRMED,
        )
        self.entry = TransferOutboxEvent.objects.create(
            reference="TRF-FAILURE-1",
            user=self.user,
            amount=Decimal("1000.00"),
            transfer_processor=PaymentProcessors.FLUTTERWAVE,
            status=TransferOutboxEvent.Status.SUBMITTED,
            wallet=self.wallet,
            transfer_request=self.withdrawal_request,
        )
        self.debit_txn = Transaction.objects.create(
            wallet=self.wallet,
            reference=self.entry.reference,
            amount=self.entry.amount,
            type=TransactionType.DEBIT,
            status=TransactionStatus.PENDING,
        )

    def webhook_data(self, **overrides):
        data = {
            "reference": self.entry.reference,
            "status": "FAILED",
            "amount": int(self.entry.amount * 100),
            "meta": {"reason": "Insufficient funds at destination bank"},
        }
        data.update(overrides)
        return data

    def test_failure_marks_failed_and_reverses_funds_to_wallet(self):
        FlutterwaveWebhookServices.parse_webhook_event(
            {"type": "transfer.failed", "data": self.webhook_data()}
        )

        self.entry.refresh_from_db()
        self.assertEqual(self.entry.status, TransferOutboxEvent.Status.FAILED)
        self.assertEqual(
            self.entry.error_log, "Insufficient funds at destination bank"
        )

        # The reversal debits the suspense transit account and credits the
        # originating wallet, making the user whole again.
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("1000.00"))
        suspense = InternalAccount.objects.get(code_name="suspense")
        self.assertEqual(suspense.balance, Decimal("-1000.00"))

        self.debit_txn.refresh_from_db()
        self.assertEqual(self.debit_txn.status, TransactionStatus.FAILED)

        self.withdrawal_request.refresh_from_db()
        self.assertEqual(
            self.withdrawal_request.status, WithdrawalRequestStatus.EXPIRED
        )

        self.log_activity.assert_called_once()
        self.send_email.assert_called_once()
        self.assertEqual(
            self.send_email.call_args.kwargs["receivers"], [self.user.email]
        )

    def test_failure_without_reason_uses_default_message(self):
        FlutterwaveWebhookServices.parse_webhook_event(
            {"type": "transfer.failed", "data": self.webhook_data(meta={})}
        )

        self.entry.refresh_from_db()
        self.assertEqual(self.entry.error_log, "Transfer failed")

    def test_failure_for_non_submitted_entry_is_a_noop(self):
        """A duplicate failure webhook - or one arriving after a local failure
        already refunded the user - must not refund twice."""
        self.entry.status = TransferOutboxEvent.Status.FAILED
        self.entry.save()

        FlutterwaveWebhookServices.parse_webhook_event(
            {"type": "transfer.failed", "data": self.webhook_data()}
        )

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("0.00"))
        self.assertEqual(
            Transaction.objects.filter(reference=self.entry.reference).count(), 1
        )
        self.withdrawal_request.refresh_from_db()
        self.assertEqual(
            self.withdrawal_request.status, WithdrawalRequestStatus.CONFIRMED
        )
        self.log_activity.assert_not_called()
        self.send_email.assert_not_called()

    def test_failure_with_unknown_reference_raises_non_retryable(self):
        with self.assertRaises(NonRetryableWebhookError):
            FlutterwaveWebhookServices.parse_webhook_event(
                {
                    "type": "transfer.failed",
                    "data": self.webhook_data(reference="TRF-UNKNOWN"),
                }
            )


class ProcessWebhookTaskTests(TestCase):
    """The task is invoked synchronously (process_webhook_task(event.id), not
    .delay) so its database side effects and return values are assertable."""

    def _make_event(self, **overrides):
        defaults = {
            "event_id": f"evt-{uuid.uuid4().hex[:8]}",
            "event_type": "transfer.disburse",
            "payload": {
                "type": "transfer.disburse",
                "data": {"reference": "TRF-REF-1", "status": "SUCCESSFUL"},
            },
            "status": "PENDING",
            "provider": PaymentProcessors.FLUTTERWAVE,
        }
        defaults.update(overrides)
        return WebhookEvent.objects.create(**defaults)

    @patch("api.webhooks.tasks.FlutterwaveWebhookServices.parse_webhook_event")
    def test_pending_event_is_processed(self, parse):
        event = self._make_event()

        process_webhook_task(event.id)

        event.refresh_from_db()
        self.assertEqual(event.status, "PROCESSED")
        self.assertIsNone(event.error_message)
        parse.assert_called_once_with(event.payload)

    @patch("api.webhooks.tasks.FlutterwaveWebhookServices.parse_webhook_event")
    def test_already_processed_event_is_skipped(self, parse):
        event = self._make_event(status="PROCESSED")

        result = process_webhook_task(event.id)

        parse.assert_not_called()
        self.assertIn("already handled", result)

    @patch("api.webhooks.tasks.FlutterwaveWebhookServices.parse_webhook_event")
    def test_in_progress_event_is_skipped(self, parse):
        event = self._make_event(status="PROCESSING")

        result = process_webhook_task(event.id)

        parse.assert_not_called()
        self.assertIn("already handled", result)

    def test_missing_row_is_reported_not_raised(self):
        result = process_webhook_task(uuid.uuid4())

        self.assertIn("missing", result)

    @patch("api.webhooks.tasks.FlutterwaveWebhookServices.parse_webhook_event")
    def test_non_retryable_error_marks_failed_without_retry(self, parse):
        parse.side_effect = NonRetryableWebhookError("unknown event type")
        event = self._make_event()

        result = process_webhook_task(event.id)

        event.refresh_from_db()
        self.assertEqual(event.status, "FAILED")
        self.assertIn("Non-retryable webhook error", event.error_message)
        self.assertIn("unknown event type", event.error_message)
        self.assertIn("failed", result)

    @patch("celery.app.task.Task.retry")
    @patch("api.webhooks.tasks.FlutterwaveWebhookServices.parse_webhook_event")
    def test_unexpected_error_marks_failed_and_schedules_retry(self, parse, retry):
        parse.side_effect = RuntimeError("db blew up")
        # Called synchronously, Task.retry would re-raise the original
        # exception; patch it to observe the scheduling decision itself.
        retry.side_effect = Retry("Task can be retried", None)
        event = self._make_event()

        with self.assertRaises(Retry):
            process_webhook_task(event.id)

        event.refresh_from_db()
        self.assertEqual(event.status, "FAILED")
        self.assertIn("Attempt 0: db blew up", event.error_message)
        retry.assert_called_once()
        self.assertEqual(retry.call_args.kwargs["countdown"], 60)
        self.assertIsInstance(retry.call_args.kwargs["exc"], RuntimeError)

    @patch("celery.app.task.Task.retry")
    def test_unsupported_provider_marks_failed_and_schedules_retry(self, retry):
        retry.side_effect = Retry("Task can be retried", None)
        event = self._make_event(provider="PAYSTACK")

        with self.assertRaises(Retry):
            process_webhook_task(event.id)

        event.refresh_from_db()
        self.assertEqual(event.status, "FAILED")
        self.assertIn("Unsupported transfer provider", event.error_message)
        retry.assert_called_once()

import json
from unittest.mock import patch

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITransactionTestCase

from core.models import YouverifyWebhookOutboxEvent


class YouverifyWebhookViewIntegrationTests(APITransactionTestCase):
	url = reverse("webhook:youverify-webhook")

	def valid_payload(self):
		return {
			"event": "identity.completed",
			"metadata": {"kyc_request_id": "kyc-request-123"},
		}

	def post_webhook(self, payload=None, signature="valid-signature"):
		return self.client.post(
			self.url,
			data=json.dumps(payload or self.valid_payload()),
			content_type="application/json",
			HTTP_X_YOUVERIFY_SIGNATURE=signature,
		)

	@patch("api.webhooks.views.youverify_webhook_views.process_youverify_webhook_task.delay")
	@patch("api.webhooks.views.youverify_webhook_views.YouverifyWebhookServices.verify_request_signature")
	def test_valid_webhook_persists_event_and_enqueues_processing(
		self, verify_signature, enqueue_task
	):
		verify_signature.return_value = True

		response = self.post_webhook()
		payload = self.valid_payload()

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.json(), {"status": "accepted"})
		event = YouverifyWebhookOutboxEvent.objects.get(
			kyc_request_id=payload["metadata"]["kyc_request_id"]
		)
		self.assertEqual(event.event_type, payload["event"])
		self.assertEqual(event.payload, payload)
		self.assertEqual(event.status, YouverifyWebhookOutboxEvent.Status.PENDING)
		enqueue_task.assert_called_once_with(event.id)

	@patch("api.webhooks.views.youverify_webhook_views.process_youverify_webhook_task.delay")
	@patch("api.webhooks.views.youverify_webhook_views.YouverifyWebhookServices.verify_request_signature")
	def test_duplicate_webhook_is_not_enqueued_twice(self, verify_signature, enqueue_task):
		verify_signature.return_value = True

		self.post_webhook()
		response = self.post_webhook()

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(YouverifyWebhookOutboxEvent.objects.count(), 1)
		enqueue_task.assert_called_once()

	@patch("api.webhooks.views.youverify_webhook_views.process_youverify_webhook_task.delay")
	@patch("api.webhooks.views.youverify_webhook_views.YouverifyWebhookServices.verify_request_signature")
	def test_real_payload_shape_with_nested_metadata_is_persisted(
		self, verify_signature, enqueue_task
	):
		"""Real YouVerify webhooks nest metadata under "data", not at top level."""
		verify_signature.return_value = True
		payload = {
			"event": "identity.completed",
			"data": {
				"status": "found",
				"metadata": {"kyc_request_id": "kyc-request-456"},
			},
		}

		response = self.post_webhook(payload)

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.json(), {"status": "accepted"})
		event = YouverifyWebhookOutboxEvent.objects.get(kyc_request_id="kyc-request-456")
		self.assertEqual(event.event_type, "identity.completed")
		enqueue_task.assert_called_once_with(event.id)

	@patch("api.webhooks.views.youverify_webhook_views.process_youverify_webhook_task.delay")
	@patch("api.webhooks.views.youverify_webhook_views.YouverifyWebhookServices.verify_request_signature")
	def test_payload_without_identifiers_is_acknowledged_without_500(
		self, verify_signature, enqueue_task
	):
		verify_signature.return_value = True

		response = self.post_webhook({"event": "identity.completed", "data": {}})

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(response.json(), {"status": "ignored"})
		self.assertEqual(YouverifyWebhookOutboxEvent.objects.count(), 0)
		enqueue_task.assert_not_called()

	@patch("api.webhooks.views.youverify_webhook_views.YouverifyWebhookServices.verify_request_signature")
	def test_invalid_signature_is_acknowledged_without_creating_event(self, verify_signature):
		verify_signature.return_value = False

		response = self.post_webhook()

		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(YouverifyWebhookOutboxEvent.objects.count(), 0)
		verify_signature.assert_called_once()

	@patch("api.webhooks.views.youverify_webhook_views.YouverifyWebhookServices.verify_request_signature")
	def test_malformed_payload_is_rejected_without_creating_event(self, verify_signature):
		verify_signature.return_value = True

		response = self.client.post(
			self.url,
			data="not-json",
			content_type="application/json",
			HTTP_X_YOUVERIFY_SIGNATURE="valid-signature",
		)

		self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
		self.assertEqual(YouverifyWebhookOutboxEvent.objects.count(), 0)

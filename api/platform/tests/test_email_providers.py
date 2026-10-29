from unittest import mock

import httpx
from django.test import SimpleTestCase, override_settings

from shared.constants.authentication import COMPANY_NAME
from shared.tasks import _send_via_cloudflare, dispatch_email

SUCCESS_RESULT = {
    "status": "sent",
    "provider": "cloudflare",
    "id": "<aB3xK9mP2qR5sT8uV0wX1yZ4cD6fG7hJ9kL0@example.com>",
}


def _cloudflare_response(status_code=200, **overrides):
    payload = {
        "success": True,
        "errors": [],
        "messages": [],
        "result": {
            "message_id": "<aB3xK9mP2qR5sT8uV0wX1yZ4cD6fG7hJ9kL0@example.com>",
            "delivered": ["qa@example.com"],
            "queued": [],
            "permanent_bounces": [],
        },
    }
    payload.update(overrides)
    response = mock.MagicMock(status_code=status_code, text='{"success": false}')
    response.json.return_value = payload
    return response


@override_settings(
    EMAIL_PROVIDER="cloudflare",
    CLOUDFLARE_API_TOKEN="test-token",
    CLOUDFLARE_ACCOUNT_ID="test-account",
    DEFAULT_FROM_EMAIL="no-reply@example.com",
)
class CloudflareEmailProviderTests(SimpleTestCase):
    URL = (
        "https://api.cloudflare.com/client/v4/accounts/"
        "test-account/email/sending/send"
    )

    def test_send_posts_expected_payload_and_headers(self):
        with mock.patch(
            "shared.tasks.httpx.post", return_value=_cloudflare_response()
        ) as post:
            result = _send_via_cloudflare(
                subject="Hello",
                recipients=["qa@example.com"],
                text_content="Plain body",
                html_content="<p>HTML body</p>",
                cc_emails=["cc@example.com"],
                bcc_emails=["bcc@example.com"],
                reply_to=["reply@example.com"],
            )

        post.assert_called_once()
        self.assertEqual(post.call_args.args[0], self.URL)
        self.assertEqual(
            post.call_args.kwargs["headers"]["Authorization"], "Bearer test-token"
        )
        self.assertEqual(
            post.call_args.kwargs["headers"]["Content-Type"], "application/json"
        )

        payload = post.call_args.kwargs["json"]
        self.assertEqual(
            payload["from"],
            {"address": "no-reply@example.com", "name": COMPANY_NAME},
        )
        self.assertEqual(payload["to"], ["qa@example.com"])
        self.assertEqual(payload["subject"], "Hello")
        self.assertEqual(payload["text"], "Plain body")
        self.assertEqual(payload["html"], "<p>HTML body</p>")
        self.assertEqual(payload["cc"], ["cc@example.com"])
        self.assertEqual(payload["bcc"], ["bcc@example.com"])
        self.assertEqual(payload["reply_to"], "reply@example.com")

        self.assertEqual(result, SUCCESS_RESULT)

    def test_dispatch_email_routes_cloudflare_provider(self):
        with mock.patch(
            "shared.tasks.httpx.post", return_value=_cloudflare_response()
        ) as post:
            result = dispatch_email(
                subject="Hello",
                recipients=["qa@example.com"],
                text_content="Plain body",
                html_content="<p>HTML body</p>",
            )

        post.assert_called_once()
        self.assertEqual(result["status"], "sent")
        self.assertEqual(result["provider"], "cloudflare")

    def test_provider_error_raises_with_cloudflare_detail(self):
        response = _cloudflare_response(
            status_code=403,
            success=False,
            errors=[{"code": 10102, "message": "Token lacks permission to send"}],
        )
        with mock.patch("shared.tasks.httpx.post", return_value=response):
            with self.assertRaises(RuntimeError) as ctx:
                _send_via_cloudflare(
                    subject="Hello",
                    recipients=["qa@example.com"],
                    text_content="Plain body",
                    html_content="<p>HTML body</p>",
                )

        self.assertIn("HTTP 403", str(ctx.exception))
        self.assertIn("Token lacks permission to send", str(ctx.exception))

    def test_transport_error_is_wrapped(self):
        with mock.patch(
            "shared.tasks.httpx.post",
            side_effect=httpx.ConnectError("Connection refused"),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                _send_via_cloudflare(
                    subject="Hello",
                    recipients=["qa@example.com"],
                    text_content="Plain body",
                    html_content="<p>HTML body</p>",
                )

        self.assertIn("Cloudflare send transport error", str(ctx.exception))

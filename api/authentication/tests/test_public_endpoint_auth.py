"""Public endpoints must ignore a stale Authorization header.

Regression cover for a Swagger-driven failure: `persistAuthorization` and
a global SECURITY requirement mean Swagger UI attaches the bearer token
to *every* request once the padlock is used, including public routes.
Those views are AllowAny, but they still inherited
DEFAULT_AUTHENTICATION_CLASSES, and DRF authenticates before it checks
permissions - so an expired or foreign token 401'd endpoints that require
no login at all.

The fix is `authentication_classes = []` on genuinely public views. These
tests pin that: a garbage token must change nothing about the outcome.
"""

from unittest.mock import patch

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

GARBAGE_BEARER = "Bearer not.a.real.token"

# (url name, HTTP method, body) for routes that must never consult a token.
PUBLIC_ROUTES = [
    ("auth-signup", "post", {}),
    ("auth-login", "post", {}),
    ("auth-forgot-password", "post", {}),
    ("auth-reset-password", "post", {}),
    ("auth-resend-verification", "post", {}),
    ("banks-list", "get", None),
]


class PublicEndpointsIgnoreStaleTokenTests(APITestCase):
    def _resolve(self, name):
        try:
            return reverse(name)
        except Exception:
            return None

    def test_stale_token_does_not_change_the_response(self):
        """The token must be inert: same status with and without it."""

        # banks-list calls out to Paystack/Flutterwave depending on settings; stub both.
        patches = [
            patch(
                "api.payments.views.bankaccount_views.PaystackService.get_banks",
                return_value=[{"name": "Access Bank", "code": "044"}],
            ),
            patch(
                "api.payments.views.bankaccount_views.FlutterwaveService.get_banks",
                return_value=[{"name": "Access Bank", "code": "044"}],
            ),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])

        checked = 0
        for name, method, body in PUBLIC_ROUTES:
            url = self._resolve(name)
            if url is None:
                continue
            checked += 1
            with self.subTest(route=name):
                self.client.credentials()
                clean = getattr(self.client, method)(url, body, format="json")

                self.client.credentials(HTTP_AUTHORIZATION=GARBAGE_BEARER)
                with_token = getattr(self.client, method)(url, body, format="json")
                self.client.credentials()

                self.assertNotEqual(
                    with_token.status_code,
                    status.HTTP_401_UNAUTHORIZED,
                    msg=(
                        f"{name} rejected a stale bearer token on a public "
                        "route - authentication_classes is not cleared."
                    ),
                )
                self.assertEqual(clean.status_code, with_token.status_code)

        self.assertGreater(checked, 0, "no public routes resolved; check url names")


class PublicViewsDeclareNoAuthenticatorsTests(APITestCase):
    """Structural check - cheaper and clearer than exercising every route."""

    def test_allow_any_views_have_no_authenticators(self):
        from api.authentication.views import auth_views, mfa_views, staff_views
        from api.payments.views import bankaccount_views
        from api.webhooks.views import (
            flutterwave_webhook_views,
            paystack_webhook_views,
        )

        public_views = [
            auth_views.SignupView,
            auth_views.VerifyEmailView,
            auth_views.ResendVerificationView,
            auth_views.LoginView,
            auth_views.ForgotPasswordView,
            auth_views.ResetPasswordView,
            auth_views.ChangeEmailConfirmView,
            staff_views.SuperAdminBootstrapView,
            staff_views.AcceptStaffInvitationView,
            mfa_views.MFAVerifyView,
            bankaccount_views.VerifyBankAccountView,
            bankaccount_views.BankListView,
            paystack_webhook_views.PaystackWebhookView,
            flutterwave_webhook_views.FlutterwaveWebhookView,
        ]

        for view in public_views:
            with self.subTest(view=view.__name__):
                self.assertEqual(
                    view.authentication_classes,
                    [],
                    msg=(
                        f"{view.__name__} is AllowAny but still runs an "
                        "authenticator, so a stale token will 401 it."
                    ),
                )

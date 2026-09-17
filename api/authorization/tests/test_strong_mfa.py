"""Money and identity actions need a session that really passed MFA."""

import pyotp
from django.core.cache import cache
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import AccessToken

from api.authentication.services import mfa_service
from api.courses.tests.factories import make_user
from api.users.enums import UserRole


def _enroll(user):
    secret = mfa_service.enroll(user=user)["secret"]
    mfa_service.confirm_enrollment(user=user, code=pyotp.TOTP(secret).now())
    return secret


@override_settings(MFA_ENFORCED=True)
class StrongMfaLoginTests(APITestCase):
    def setUp(self):
        cache.clear()

    def _login(self, user):
        return self.client.post(
            "/api/v1/auth/login/",
            {"email": user.email, "password": "testpass123"},
            format="json",
        )

    def test_a_non_mandated_role_with_a_device_is_now_challenged(self):
        writer = make_user(role=UserRole.STAFF_WRITER)
        _enroll(writer)

        response = self._login(writer)

        self.assertTrue(response.data["mfa_required"])
        self.assertNotIn("access", response.data)

    def test_a_non_mandated_role_without_a_device_is_not_challenged_and_not_marked_challenged(
        self,
    ):
        writer = make_user(role=UserRole.STAFF_WRITER)

        response = self._login(writer)

        access = AccessToken(response.data["access"])
        self.assertTrue(access["mfa_verified"])
        self.assertFalse(access["mfa_challenged"])

    def test_passing_the_challenge_marks_the_session_challenged(self):
        writer = make_user(role=UserRole.STAFF_WRITER)
        secret = _enroll(writer)
        challenge = self._login(writer).data["challenge_token"]

        response = self.client.post(
            "/api/v1/auth/mfa/verify/",
            {"challenge_token": challenge, "code": pyotp.TOTP(secret).now()},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(AccessToken(response.data["access"])["mfa_challenged"])


@override_settings(MFA_ENFORCED=True)
class StaleRoleClaimTests(APITestCase):
    def test_mfa_state_does_not_carry_across_a_role_change(self):
        admin = make_user(role=UserRole.ADMIN)
        self.client.force_authenticate(
            admin, token={"mfa_verified": True, "role": UserRole.STAFF_WRITER}
        )

        response = self.client.patch(
            "/api/v1/platform-settings/",
            {"topic_reservation_expiry_days": 20},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

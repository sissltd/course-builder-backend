from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.core import mail
from django.core.cache import cache
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
from rest_framework_simplejwt.tokens import RefreshToken

from api.authentication.enums import TokenPurpose
from api.authentication.models import EmailVerificationToken
from api.authentication.tests.factories import make_user, make_verification_token
from api.notification.models import Notification
from api.users.enums import AccountStatus, UserRole
from api.users.models import UserActivityLog


class SignupApiTests(APITestCase):
    def setUp(self):
        # Throttling is cache-backed (shared Redis, not DB-transaction-scoped
        # like everything else in a TestCase), so it must be reset per test -
        # otherwise unrelated test methods sharing the test client's fake IP
        # would trip each other's rate limit.
        cache.clear()

    def test_happy_path(self):
        # signup() emails via transaction.on_commit(), which never fires under
        # TestCase's rolled-back outer transaction unless captured like this.
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                "/api/v1/auth/signup/",
                {
                    "email": "creator@example.com",
                    "password": "StrongPass123!",
                    "password_confirm": "StrongPass123!",
                    "first_name": "Ada",
                    "last_name": "Lovelace",
                    "country": "NG",
                    "terms_accepted": True,
                },
                format="json",
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertFalse(response.data["is_active"])
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(f"{settings.FRONTEND_URL}/verify-email", mail.outbox[0].body)
        self.assertTrue(
            UserActivityLog.objects.filter(
                user__email="creator@example.com", action="ACCOUNT_CREATED"
            ).exists()
        )

    def test_duplicate_email_rejected(self):
        make_user(email="dupe@example.com")
        response = self.client.post(
            "/api/v1/auth/signup/",
            {
                "email": "dupe@example.com",
                "password": "StrongPass123!",
                "password_confirm": "StrongPass123!",
                "first_name": "A",
                "last_name": "B",
                "country": "NG",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        # Generic message - doesn't confirm the email is already registered.
        self.assertIn(
            "Unable to create account",
            " ".join(error["message"] for error in response.data["errors"]),
        )

    def test_creates_course_creator_role_by_default(self):
        response = self.client.post(
            "/api/v1/auth/signup/",
            {
                "email": "defaultrole@example.com",
                "password": "StrongPass123!",
                "password_confirm": "StrongPass123!",
                "first_name": "A",
                "last_name": "B",
                "country": "NG",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["role"], "COURSE_CREATOR")

    def test_mismatched_password_confirm_rejected(self):
        response = self.client.post(
            "/api/v1/auth/signup/",
            {
                "email": "mismatch@example.com",
                "password": "StrongPass123!",
                "password_confirm": "DifferentPass456!",
                "first_name": "A",
                "last_name": "B",
                "country": "NG",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(
            any(
                error["field_name"] == "password_confirm"
                for error in response.data["errors"]
            )
        )

    def test_missing_country_rejected(self):
        response = self.client.post(
            "/api/v1/auth/signup/",
            {
                "email": "nocountry@example.com",
                "password": "StrongPass123!",
                "first_name": "A",
                "last_name": "B",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(
            any(error["field_name"] == "country" for error in response.data["errors"])
        )

    def test_terms_accepted_defaults_false(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                "/api/v1/auth/signup/",
                {
                    "email": "noterms@example.com",
                    "password": "StrongPass123!",
                    "password_confirm": "StrongPass123!",
                    "first_name": "A",
                    "last_name": "B",
                    "country": "NG",
                },
                format="json",
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(response.data["terms_accepted_at"])


class ReviewerSignupApiTests(APITestCase):
    def setUp(self):
        cache.clear()

    def test_happy_path_creates_creator_reviewer_role(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                "/api/v1/auth/reviewer/signup/",
                {
                    "email": "reviewer@example.com",
                    "password": "StrongPass123!",
                    "password_confirm": "StrongPass123!",
                    "first_name": "Rita",
                    "last_name": "Reviewer",
                    "country": "NG",
                },
                format="json",
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["role"], "CREATOR_REVIEWER")
        self.assertFalse(response.data["is_active"])
        self.assertEqual(len(mail.outbox), 1)


class VerifyEmailApiTests(APITestCase):
    def test_happy_path_returns_tokens(self):
        user = make_user(is_active=False)
        make_verification_token(
            user=user, purpose=TokenPurpose.SIGNUP_VERIFICATION, raw_token="verify-me"
        )

        response = self.client.post(
            "/api/v1/auth/verify-email/",
            {"email": user.email, "token": "verify-me"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)

    def test_verify_email_sets_status_to_active(self):
        user = make_user(is_active=False, status=AccountStatus.PENDING_VERIFICATION)
        make_verification_token(
            user=user, purpose=TokenPurpose.SIGNUP_VERIFICATION, raw_token="verify-me"
        )

        self.client.post(
            "/api/v1/auth/verify-email/",
            {"email": user.email, "token": "verify-me"},
            format="json",
        )
        user.refresh_from_db()
        self.assertEqual(user.status, AccountStatus.ACTIVE)

    def test_wrong_token_does_not_activate(self):
        user = make_user(is_active=False)
        make_verification_token(
            user=user, purpose=TokenPurpose.SIGNUP_VERIFICATION, raw_token="verify-me"
        )

        response = self.client.post(
            "/api/v1/auth/verify-email/",
            {"email": user.email, "token": "wrong-token"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        user.refresh_from_db()
        self.assertFalse(user.is_active)


class ResendVerificationApiTests(APITestCase):
    def test_before_cooldown_rejected(self):
        user = make_user()
        make_verification_token(user=user, purpose=TokenPurpose.SIGNUP_VERIFICATION)

        response = self.client.post(
            "/api/v1/auth/resend-verification/",
            {"email": user.email, "purpose": "SIGNUP_VERIFICATION"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_after_cooldown_issues_new_token(self):
        user = make_user()
        record = make_verification_token(
            user=user, purpose=TokenPurpose.SIGNUP_VERIFICATION
        )
        EmailVerificationToken.objects.filter(pk=record.pk).update(
            created_datetime=timezone.now() - timedelta(seconds=61)
        )

        response = self.client.post(
            "/api/v1/auth/resend-verification/",
            {"email": user.email, "purpose": "SIGNUP_VERIFICATION"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class LoginApiTests(APITestCase):
    def setUp(self):
        cache.clear()

    def test_unverified_account_rejected(self):
        make_user(
            email="inactive@example.com", password="StrongPass123!", is_active=False
        )

        response = self.client.post(
            "/api/v1/auth/login/",
            {"email": "inactive@example.com", "password": "StrongPass123!"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(
            any(error["field_name"] == "email" for error in response.data["errors"])
        )

    def test_wrong_password_rejected(self):
        make_user(email="user@example.com", password="StrongPass123!")

        response = self.client.post(
            "/api/v1/auth/login/",
            {"email": "user@example.com", "password": "WrongPass!"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        # Generic non-field message - doesn't reveal whether the email or the
        # password was the wrong part (anti-enumeration).
        self.assertTrue(
            any(
                error["field_name"] == "non_field_errors"
                for error in response.data["errors"]
            )
        )
        self.assertIn(
            "Invalid email or password",
            " ".join(error["message"] for error in response.data["errors"]),
        )

    def test_wrong_password_increments_failed_login_attempts(self):
        user = make_user(email="user@example.com", password="StrongPass123!")
        self.assertEqual(user.failed_login_attempts, 0)

        self.client.post(
            "/api/v1/auth/login/",
            {"email": "user@example.com", "password": "WrongPass1!"},
            format="json",
        )
        user.refresh_from_db()
        self.assertEqual(user.failed_login_attempts, 1)

        self.client.post(
            "/api/v1/auth/login/",
            {"email": "user@example.com", "password": "WrongPass2!"},
            format="json",
        )
        user.refresh_from_db()
        self.assertEqual(user.failed_login_attempts, 2)

    def test_successful_login_resets_failed_login_attempts(self):
        user = make_user(email="user@example.com", password="StrongPass123!")
        # Manually set failed attempts to simulate prior failures
        user.failed_login_attempts = 3
        user.save()

        self.client.post(
            "/api/v1/auth/login/",
            {"email": "user@example.com", "password": "StrongPass123!"},
            format="json",
        )
        user.refresh_from_db()
        self.assertEqual(user.failed_login_attempts, 0)

    def test_nonexistent_email_rejected(self):
        response = self.client.post(
            "/api/v1/auth/login/",
            {"email": "nobody@example.com", "password": "WhateverPass1!"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        # Same generic message as a wrong password - a nonexistent email must
        # not be distinguishable from a real one with the wrong password.
        self.assertTrue(
            any(
                error["field_name"] == "non_field_errors"
                for error in response.data["errors"]
            )
        )
        self.assertIn(
            "Invalid email or password",
            " ".join(error["message"] for error in response.data["errors"]),
        )

    def test_missing_email_and_password_rejected(self):
        response = self.client.post("/api/v1/auth/login/", {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        field_names = {error["field_name"] for error in response.data["errors"]}
        self.assertIn("email", field_names)
        self.assertIn("password", field_names)

    def test_happy_path(self):
        user = make_user(email="user2@example.com", password="StrongPass123!")

        response = self.client.post(
            "/api/v1/auth/login/",
            {"email": "user2@example.com", "password": "StrongPass123!"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
        self.assertTrue(
            UserActivityLog.objects.filter(user=user, action="LOGIN").exists()
        )

    def test_reviewer_login_endpoint_works(self):
        make_user(
            email="reviewer2@example.com",
            password="StrongPass123!",
            role=UserRole.CREATOR_REVIEWER,
        )

        response = self.client.post(
            "/api/v1/auth/reviewer/login/",
            {"email": "reviewer2@example.com", "password": "StrongPass123!"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)


@patch.dict(ScopedRateThrottle.THROTTLE_RATES, {"login": "1000/min"})
class AccountLockoutApiTests(APITestCase):
    """Login's own throttle rate is patched way up here so these tests can
    make several login attempts in a row without tripping the real 5/min
    rate limit - that's covered separately in ThrottleApiTests.

    Patches ScopedRateThrottle.THROTTLE_RATES directly (not
    @override_settings(REST_FRAMEWORK=...)) because DRF's throttle classes
    bind THROTTLE_RATES = api_settings.DEFAULT_THROTTLE_RATES once, at
    rest_framework.throttling's import time - a later Django setting_changed
    signal doesn't reach that already-bound class attribute, so
    override_settings silently has no effect on throttle rates in tests.
    """

    def setUp(self):
        cache.clear()

    def _fail_login(self, email, password="WrongPass!"):
        return self.client.post(
            "/api/v1/auth/login/",
            {"email": email, "password": password},
            format="json",
        )

    def test_fifth_failure_locks_account(self):
        user = make_user(email="lockme1@example.com", password="StrongPass123!")

        for _ in range(5):
            response = self._fail_login(user.email)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        user.refresh_from_db()
        self.assertIsNotNone(user.locked_until)
        self.assertGreater(user.locked_until, timezone.now())

    def test_locked_account_rejects_even_correct_password(self):
        user = make_user(email="lockme2@example.com", password="StrongPass123!")
        for _ in range(5):
            self._fail_login(user.email)

        response = self.client.post(
            "/api/v1/auth/login/",
            {"email": user.email, "password": "StrongPass123!"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(
            "Too many failed attempts",
            " ".join(str(e) for e in response.data["errors"]),
        )

    def test_expired_lock_no_longer_blocks_login(self):
        user = make_user(email="lockme3@example.com", password="StrongPass123!")
        user.locked_until = timezone.now() - timedelta(minutes=1)
        user.failed_login_attempts = 5
        user.save(update_fields=["locked_until", "failed_login_attempts"])

        response = self.client.post(
            "/api/v1/auth/login/",
            {"email": user.email, "password": "StrongPass123!"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        user.refresh_from_db()
        self.assertIsNone(user.locked_until)
        self.assertEqual(user.failed_login_attempts, 0)

    def test_lockout_logs_and_emails_owner(self):
        user = make_user(email="lockme4@example.com", password="StrongPass123!")

        for _ in range(5):
            self._fail_login(user.email)

        self.assertTrue(
            UserActivityLog.objects.filter(
                user=user, action="LOCKOUT_TRIGGERED"
            ).exists()
        )
        self.assertTrue(
            any(
                m.subject == "Your account was temporarily locked"
                for m in mail.outbox
            )
        )

    def test_first_lockout_does_not_alert_admins(self):
        admin = make_user(email="admin1@example.com", role=UserRole.ADMIN)
        user = make_user(email="lockme5@example.com", password="StrongPass123!")

        for _ in range(5):
            self._fail_login(user.email)

        self.assertFalse(
            Notification.objects.filter(
                receiver=admin, title="Repeated login lockouts"
            ).exists()
        )

    def test_second_lockout_within_24h_alerts_admins(self):
        admin = make_user(email="admin2@example.com", role=UserRole.ADMIN)
        user = make_user(email="lockme6@example.com", password="StrongPass123!")

        for _ in range(5):
            self._fail_login(user.email)

        # Simulate the first lock naturally expiring (rather than waiting 15
        # real minutes) so a second run of failures can trigger lockout #2.
        user.refresh_from_db()
        user.locked_until = None
        user.failed_login_attempts = 0
        user.save(update_fields=["locked_until", "failed_login_attempts"])

        for _ in range(5):
            self._fail_login(user.email)

        self.assertTrue(
            Notification.objects.filter(
                receiver=admin, title="Repeated login lockouts"
            ).exists()
        )


class ThrottleApiTests(APITestCase):
    """Exercises the real configured throttle rates (unlike
    AccountLockoutApiTests, which raises login's rate out of the way)."""

    def setUp(self):
        cache.clear()

    def test_login_rate_limited(self):
        make_user(email="throttle-login@example.com", password="StrongPass123!")

        for _ in range(5):
            response = self.client.post(
                "/api/v1/auth/login/",
                {"email": "throttle-login@example.com", "password": "wrong"},
                format="json",
            )
            self.assertNotEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

        response = self.client.post(
            "/api/v1/auth/login/",
            {"email": "throttle-login@example.com", "password": "wrong"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_signup_rate_limited(self):
        for i in range(3):
            response = self.client.post(
                "/api/v1/auth/signup/",
                {
                    "email": f"throttle-signup-{i}@example.com",
                    "password": "StrongPass123!",
                    "password_confirm": "StrongPass123!",
                    "first_name": "A",
                    "last_name": "B",
                    "country": "NG",
                },
                format="json",
            )
            self.assertNotEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

        response = self.client.post(
            "/api/v1/auth/signup/",
            {
                "email": "throttle-signup-extra@example.com",
                "password": "StrongPass123!",
                "password_confirm": "StrongPass123!",
                "first_name": "A",
                "last_name": "B",
                "country": "NG",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_forgot_password_rate_limited(self):
        for _ in range(3):
            response = self.client.post(
                "/api/v1/auth/forgot-password/",
                {"email": "nobody@example.com"},
                format="json",
            )
            self.assertNotEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

        response = self.client.post(
            "/api/v1/auth/forgot-password/",
            {"email": "nobody@example.com"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_reset_password_rate_limited(self):
        for _ in range(5):
            response = self.client.post(
                "/api/v1/auth/reset-password/",
                {
                    "email": "nobody@example.com",
                    "token": "bad-token",
                    "new_password": "StrongPass123!",
                },
                format="json",
            )
            self.assertNotEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

        response = self.client.post(
            "/api/v1/auth/reset-password/",
            {
                "email": "nobody@example.com",
                "token": "bad-token",
                "new_password": "StrongPass123!",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)


class TokenRefreshApiTests(APITestCase):
    def test_happy_path(self):
        user = make_user()
        refresh = RefreshToken.for_user(user)

        response = self.client.post(
            "/api/v1/auth/token/refresh/", {"refresh": str(refresh)}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)

    def test_blacklisted_token_rejected(self):
        user = make_user()
        refresh = RefreshToken.for_user(user)
        refresh.blacklist()

        response = self.client.post(
            "/api/v1/auth/token/refresh/", {"refresh": str(refresh)}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_suspended_user_cannot_refresh(self):
        user = make_user()
        refresh = RefreshToken.for_user(user)
        user.status = AccountStatus.SUSPENDED
        user.save(update_fields=["status"])

        response = self.client.post(
            "/api/v1/auth/token/refresh/", {"refresh": str(refresh)}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_deactivated_user_cannot_refresh(self):
        user = make_user()
        refresh = RefreshToken.for_user(user)
        user.is_active = False
        user.status = AccountStatus.DEACTIVATED
        user.save(update_fields=["is_active", "status"])

        response = self.client.post(
            "/api/v1/auth/token/refresh/", {"refresh": str(refresh)}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class LogoutApiTests(APITestCase):
    def test_happy_path_blacklists_and_logs(self):
        user = make_user()
        refresh = RefreshToken.for_user(user)
        self.client.force_authenticate(user)

        response = self.client.post(
            "/api/v1/auth/logout/", {"refresh": str(refresh)}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(
            BlacklistedToken.objects.filter(token__jti=refresh["jti"]).exists()
        )
        self.assertTrue(
            UserActivityLog.objects.filter(user=user, action="LOGOUT").exists()
        )

    def test_requires_authentication(self):
        user = make_user()
        refresh = RefreshToken.for_user(user)

        response = self.client.post(
            "/api/v1/auth/logout/", {"refresh": str(refresh)}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class LogoutAllApiTests(APITestCase):
    def test_requires_authentication(self):
        response = self.client.post("/api/v1/auth/logout-all/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_blacklists_every_outstanding_token(self):
        user = make_user()
        refresh1 = RefreshToken.for_user(user)
        refresh2 = RefreshToken.for_user(user)
        self.client.force_authenticate(user)

        response = self.client.post("/api/v1/auth/logout-all/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(
            BlacklistedToken.objects.filter(token__jti=refresh1["jti"]).exists()
        )
        self.assertTrue(
            BlacklistedToken.objects.filter(token__jti=refresh2["jti"]).exists()
        )

        # Both tokens are now unusable at the refresh endpoint too.
        for refresh in (refresh1, refresh2):
            refresh_response = self.client.post(
                "/api/v1/auth/token/refresh/", {"refresh": str(refresh)}, format="json"
            )
            self.assertEqual(
                refresh_response.status_code, status.HTTP_401_UNAUTHORIZED
            )

    def test_logs_sessions_revoked_activity(self):
        user = make_user()
        RefreshToken.for_user(user)
        self.client.force_authenticate(user)

        self.client.post("/api/v1/auth/logout-all/")

        self.assertTrue(
            UserActivityLog.objects.filter(
                user=user, action="SESSIONS_REVOKED"
            ).exists()
        )

    def test_only_affects_the_authenticated_users_tokens(self):
        user = make_user()
        other_user = make_user()
        user_refresh = RefreshToken.for_user(user)
        other_refresh = RefreshToken.for_user(other_user)
        self.client.force_authenticate(user)

        self.client.post("/api/v1/auth/logout-all/")

        self.assertTrue(
            BlacklistedToken.objects.filter(token__jti=user_refresh["jti"]).exists()
        )
        self.assertFalse(
            BlacklistedToken.objects.filter(token__jti=other_refresh["jti"]).exists()
        )


class UserSessionApiTests(APITestCase):
    def setUp(self):
        cache.clear()

    def _login(self, email, password="testpass123"):
        response = self.client.post(
            "/api/v1/auth/login/",
            {"email": email, "password": password},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.data

    def test_login_creates_a_session_with_matching_sid(self):
        from api.authentication.models import UserSession
        from rest_framework_simplejwt.tokens import AccessToken

        user = make_user(email="sessioner@example.com")
        tokens = self._login(user.email)

        self.assertEqual(UserSession.objects.filter(user=user).count(), 1)
        session = UserSession.objects.get(user=user)
        access = AccessToken(tokens["access"])
        self.assertEqual(access.get("sid"), str(session.id))

    def test_requires_authentication(self):
        response = self.client.get("/api/v1/auth/sessions/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_list_returns_own_sessions_with_is_current_flag(self):
        user = make_user(email="lister@example.com")
        tokens = self._login(user.email)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

        response = self.client.get("/api/v1/auth/sessions/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["data"]["results"]
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["is_current"])

    def test_refresh_keeps_the_same_session_and_bumps_last_seen(self):
        from api.authentication.models import UserSession

        user = make_user(email="refresher@example.com")
        tokens = self._login(user.email)
        session = UserSession.objects.get(user=user)
        original_last_seen = session.last_seen_at

        refresh_response = self.client.post(
            "/api/v1/auth/token/refresh/",
            {"refresh": tokens["refresh"]},
            format="json",
        )
        self.assertEqual(refresh_response.status_code, status.HTTP_200_OK)

        session.refresh_from_db()
        self.assertEqual(UserSession.objects.filter(user=user).count(), 1)
        self.assertGreaterEqual(session.last_seen_at, original_last_seen)

    def test_revoke_blacklists_the_sessions_current_refresh_token(self):
        from api.authentication.models import UserSession

        user = make_user(email="revoker@example.com")
        tokens = self._login(user.email)
        session = UserSession.objects.get(user=user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

        response = self.client.delete(f"/api/v1/auth/sessions/{session.id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        session.refresh_from_db()
        self.assertIsNotNone(session.revoked_at)

        refresh_response = self.client.post(
            "/api/v1/auth/token/refresh/",
            {"refresh": tokens["refresh"]},
            format="json",
        )
        self.assertEqual(refresh_response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_revoke_another_users_session_returns_404(self):
        user = make_user(email="owner@example.com")
        other_user = make_user(email="attacker@example.com")
        from api.authentication.models import UserSession

        self._login(user.email)
        session = UserSession.objects.get(user=user)

        other_tokens = self._login(other_user.email)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {other_tokens['access']}")

        response = self.client.delete(f"/api/v1/auth/sessions/{session.id}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_logout_all_empties_the_sessions_list(self):
        user = make_user(email="logoutall@example.com")
        tokens = self._login(user.email)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

        self.client.post("/api/v1/auth/logout-all/")

        response = self.client.get("/api/v1/auth/sessions/")
        self.assertEqual(response.data["data"]["results"], [])


class ForgotPasswordApiTests(APITestCase):
    def test_existing_and_nonexistent_email_both_return_200(self):
        user = make_user()

        existing_response = self.client.post(
            "/api/v1/auth/forgot-password/", {"email": user.email}, format="json"
        )
        nonexistent_response = self.client.post(
            "/api/v1/auth/forgot-password/",
            {"email": "ghost@example.com"},
            format="json",
        )

        self.assertEqual(existing_response.status_code, status.HTTP_200_OK)
        self.assertEqual(nonexistent_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            len(mail.outbox), 1
        )  # only the existing user actually gets an email
        self.assertIn(f"{settings.FRONTEND_URL}/reset-password", mail.outbox[0].body)


class ResetPasswordApiTests(APITestCase):
    def test_happy_path_allows_login_with_new_password(self):
        user = make_user(email="resetme@example.com", password="OldPass123!")
        make_verification_token(
            user=user, purpose=TokenPurpose.PASSWORD_RESET, raw_token="reset-me"
        )

        response = self.client.post(
            "/api/v1/auth/reset-password/",
            {
                "email": user.email,
                "token": "reset-me",
                "new_password": "BrandNewPass456!",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        login_response = self.client.post(
            "/api/v1/auth/login/",
            {"email": user.email, "password": "BrandNewPass456!"},
            format="json",
        )
        self.assertEqual(login_response.status_code, status.HTTP_200_OK)

    def test_revokes_existing_sessions_and_sends_confirmation(self):
        user = make_user(email="resetme2@example.com", password="OldPass123!")
        refresh = RefreshToken.for_user(user)
        make_verification_token(
            user=user, purpose=TokenPurpose.PASSWORD_RESET, raw_token="reset-me"
        )

        response = self.client.post(
            "/api/v1/auth/reset-password/",
            {
                "email": user.email,
                "token": "reset-me",
                "new_password": "BrandNewPass456!",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertTrue(
            BlacklistedToken.objects.filter(token__jti=refresh["jti"]).exists()
        )
        refresh_response = self.client.post(
            "/api/v1/auth/token/refresh/", {"refresh": str(refresh)}, format="json"
        )
        self.assertEqual(refresh_response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(mail.outbox[-1].subject, "Your password was changed")

    def test_expired_token_rejected(self):
        user = make_user()
        make_verification_token(
            user=user,
            purpose=TokenPurpose.PASSWORD_RESET,
            raw_token="reset-me",
            expires_in_minutes=-1,
        )

        response = self.client.post(
            "/api/v1/auth/reset-password/",
            {
                "email": user.email,
                "token": "reset-me",
                "new_password": "BrandNewPass456!",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class ChangePasswordApiTests(APITestCase):
    def setUp(self):
        cache.clear()

    def test_requires_authentication(self):
        response = self.client.post(
            "/api/v1/auth/change-password/",
            {"current_password": "x", "new_password": "y"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_happy_path_allows_login_with_new_password(self):
        user = make_user(password="OldPass123!")
        self.client.force_authenticate(user)

        response = self.client.post(
            "/api/v1/auth/change-password/",
            {"current_password": "OldPass123!", "new_password": "BrandNewPass456!"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        login_response = self.client.post(
            "/api/v1/auth/login/",
            {"email": user.email, "password": "BrandNewPass456!"},
            format="json",
        )
        self.assertEqual(login_response.status_code, status.HTTP_200_OK)

    def test_wrong_current_password_rejected(self):
        user = make_user(password="OldPass123!")
        self.client.force_authenticate(user)

        response = self.client.post(
            "/api/v1/auth/change-password/",
            {"current_password": "WrongPass!", "new_password": "BrandNewPass456!"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_weak_new_password_rejected(self):
        user = make_user(password="OldPass123!")
        self.client.force_authenticate(user)

        response = self.client.post(
            "/api/v1/auth/change-password/",
            {"current_password": "OldPass123!", "new_password": "123"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_logs_password_changed_activity(self):
        user = make_user(password="OldPass123!")
        self.client.force_authenticate(user)

        self.client.post(
            "/api/v1/auth/change-password/",
            {"current_password": "OldPass123!", "new_password": "BrandNewPass456!"},
            format="json",
        )

        self.assertTrue(
            UserActivityLog.objects.filter(
                user=user, action="PASSWORD_CHANGED", category="AUTH"
            ).exists()
        )


class ChangeEmailApiTests(APITestCase):
    def setUp(self):
        # This class logs in via /auth/login/ (a throttled, cache-backed
        # endpoint) in some tests - reset per test so it doesn't inherit
        # throttle state from whichever class ran before it.
        cache.clear()

    def test_request_requires_authentication(self):
        response = self.client.post(
            "/api/v1/auth/change-email/",
            {"new_email": "new@example.com", "password": "x"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_request_wrong_password_rejected(self):
        user = make_user(email="old@example.com", password="StrongPass123!")
        self.client.force_authenticate(user)

        response = self.client.post(
            "/api/v1/auth/change-email/",
            {"new_email": "new@example.com", "password": "WrongPass!"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_request_duplicate_email_rejected(self):
        make_user(email="taken@example.com")
        user = make_user(email="old@example.com", password="StrongPass123!")
        self.client.force_authenticate(user)

        response = self.client.post(
            "/api/v1/auth/change-email/",
            {"new_email": "taken@example.com", "password": "StrongPass123!"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_request_sends_confirmation_to_new_address_without_changing_email_yet(self):
        user = make_user(email="old@example.com", password="StrongPass123!")
        self.client.force_authenticate(user)

        response = self.client.post(
            "/api/v1/auth/change-email/",
            {"new_email": "new@example.com", "password": "StrongPass123!"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["new@example.com"])
        user.refresh_from_db()
        self.assertEqual(user.email, "old@example.com")

    def test_confirm_updates_email_and_old_email_stops_working(self):
        user = make_user(email="old@example.com", password="StrongPass123!")
        make_verification_token(
            user=user,
            purpose=TokenPurpose.EMAIL_CHANGE,
            raw_token="confirm-me",
            new_email="new@example.com",
        )

        response = self.client.post(
            "/api/v1/auth/change-email/confirm/",
            {"token": "confirm-me"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        user.refresh_from_db()
        self.assertEqual(user.email, "new@example.com")

        old_login = self.client.post(
            "/api/v1/auth/login/",
            {"email": "old@example.com", "password": "StrongPass123!"},
            format="json",
        )
        self.assertEqual(old_login.status_code, status.HTTP_400_BAD_REQUEST)

        new_login = self.client.post(
            "/api/v1/auth/login/",
            {"email": "new@example.com", "password": "StrongPass123!"},
            format="json",
        )
        self.assertEqual(new_login.status_code, status.HTTP_200_OK)

    def test_confirm_wrong_token_rejected(self):
        response = self.client.post(
            "/api/v1/auth/change-email/confirm/",
            {"token": "not-a-real-token"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_confirm_expired_token_rejected(self):
        user = make_user(email="old@example.com")
        make_verification_token(
            user=user,
            purpose=TokenPurpose.EMAIL_CHANGE,
            raw_token="confirm-me",
            new_email="new@example.com",
            expires_in_minutes=-1,
        )

        response = self.client.post(
            "/api/v1/auth/change-email/confirm/",
            {"token": "confirm-me"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class MeApiTests(APITestCase):
    def test_authenticated(self):
        user = make_user()
        self.client.force_authenticate(user)

        response = self.client.get("/api/v1/users/me/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["email"], user.email)

    def test_unauthenticated(self):
        response = self.client.get("/api/v1/users/me/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_patch_updates_profile_fields(self):
        user = make_user()
        self.client.force_authenticate(user)

        response = self.client.patch(
            "/api/v1/users/me/",
            {"first_name": "Updated", "timezone": "Africa/Lagos"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["first_name"], "Updated")
        self.assertEqual(response.data["timezone"], "Africa/Lagos")
        user.refresh_from_db()
        self.assertEqual(user.first_name, "Updated")

    def test_patch_cannot_change_email(self):
        user = make_user(email="original@example.com")
        self.client.force_authenticate(user)

        response = self.client.patch(
            "/api/v1/users/me/",
            {"email": "changed@example.com"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        user.refresh_from_db()
        self.assertEqual(user.email, "original@example.com")

    def test_patch_logs_configuration_activity(self):
        user = make_user()
        self.client.force_authenticate(user)

        self.client.patch("/api/v1/users/me/", {"first_name": "New"}, format="json")

        self.assertTrue(
            UserActivityLog.objects.filter(
                user=user, action="PROFILE_UPDATED", category="CONFIGURATION"
            ).exists()
        )

import pyotp
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from api.authentication.models import MFAChallenge, MFARecoveryCode
from api.authentication.services import mfa_service
from api.authentication.tests.factories import make_user
from api.users.enums import UserRole
from api.users.models import UserActivityLog


def _enroll_and_confirm(user):
    """Full enrollment round trip via the service layer - returns
    (secret, recovery_codes)."""

    result = mfa_service.enroll(user=user)
    secret = result["secret"]
    code = pyotp.TOTP(secret).now()
    recovery_codes = mfa_service.confirm_enrollment(user=user, code=code)
    return secret, recovery_codes


class MFAServiceTests(TestCase):
    def test_enroll_then_confirm_enables_device_and_issues_recovery_codes(self):
        user = make_user(role=UserRole.ADMIN)

        secret, recovery_codes = _enroll_and_confirm(user)

        device = mfa_service.get_device(user=user)
        self.assertTrue(device.is_enabled)
        self.assertIsNotNone(device.enrolled_at)
        self.assertEqual(len(recovery_codes), mfa_service.RECOVERY_CODE_COUNT)
        self.assertEqual(
            MFARecoveryCode.objects.filter(user=user, used_at__isnull=True).count(),
            mfa_service.RECOVERY_CODE_COUNT,
        )
        self.assertTrue(
            UserActivityLog.objects.filter(user=user, action="MFA_ENABLED").exists()
        )

    def test_confirm_with_wrong_code_does_not_enable(self):
        user = make_user(role=UserRole.ADMIN)
        mfa_service.enroll(user=user)

        with self.assertRaises(Exception):
            mfa_service.confirm_enrollment(user=user, code="000000")

        device = mfa_service.get_device(user=user)
        self.assertFalse(device.is_enabled)

    def test_re_enrolling_overwrites_pending_secret(self):
        user = make_user(role=UserRole.ADMIN)
        first = mfa_service.enroll(user=user)
        second = mfa_service.enroll(user=user)

        self.assertNotEqual(first["secret"], second["secret"])
        # The old secret must not be confirmable anymore.
        old_code = pyotp.TOTP(first["secret"]).now()
        with self.assertRaises(Exception):
            mfa_service.confirm_enrollment(user=user, code=old_code)

    def test_verify_challenge_accepts_live_totp_code(self):
        user = make_user(role=UserRole.ADMIN)
        secret, _codes = _enroll_and_confirm(user)
        challenge_token = mfa_service.create_challenge(user=user)

        result = mfa_service.verify_challenge(
            challenge_token=challenge_token, code=pyotp.TOTP(secret).now()
        )
        self.assertEqual(result.id, user.id)

    def test_verify_challenge_rejects_replayed_code(self):
        user = make_user(role=UserRole.ADMIN)
        secret, _codes = _enroll_and_confirm(user)
        code = pyotp.TOTP(secret).now()

        challenge1 = mfa_service.create_challenge(user=user)
        mfa_service.verify_challenge(challenge_token=challenge1, code=code)

        challenge2 = mfa_service.create_challenge(user=user)
        with self.assertRaises(Exception):
            mfa_service.verify_challenge(challenge_token=challenge2, code=code)

    def test_verify_challenge_accepts_recovery_code_once(self):
        user = make_user(role=UserRole.ADMIN)
        _secret, recovery_codes = _enroll_and_confirm(user)
        recovery_code = recovery_codes[0]

        challenge1 = mfa_service.create_challenge(user=user)
        mfa_service.verify_challenge(challenge_token=challenge1, code=recovery_code)

        challenge2 = mfa_service.create_challenge(user=user)
        with self.assertRaises(Exception):
            mfa_service.verify_challenge(challenge_token=challenge2, code=recovery_code)

    def test_verify_challenge_generic_error_for_unknown_challenge(self):
        with self.assertRaises(Exception) as ctx:
            mfa_service.verify_challenge(challenge_token="garbage", code="123456")
        self.assertEqual(str(ctx.exception.detail[0]), mfa_service._GENERIC_MFA_ERROR)

    def test_expired_challenge_rejected(self):
        user = make_user(role=UserRole.ADMIN)
        secret, _codes = _enroll_and_confirm(user)
        challenge_token = mfa_service.create_challenge(user=user)
        MFAChallenge.objects.filter(user=user).update(
            expires_at=timezone.now() - timezone.timedelta(minutes=1)
        )

        with self.assertRaises(Exception):
            mfa_service.verify_challenge(
                challenge_token=challenge_token, code=pyotp.TOTP(secret).now()
            )

    def test_lockout_after_max_failed_attempts(self):
        user = make_user(role=UserRole.ADMIN)
        _enroll_and_confirm(user)

        for _ in range(mfa_service.MFA_MAX_FAILED_ATTEMPTS):
            challenge_token = mfa_service.create_challenge(user=user)
            with self.assertRaises(Exception):
                mfa_service.verify_challenge(
                    challenge_token=challenge_token, code="000000"
                )

        user.refresh_from_db()
        self.assertTrue(mfa_service.is_locked_out(user=user))

    def test_regenerate_recovery_codes_requires_fresh_totp(self):
        user = make_user(role=UserRole.ADMIN)
        secret, old_codes = _enroll_and_confirm(user)

        new_codes = mfa_service.regenerate_recovery_codes(
            user=user, code=pyotp.TOTP(secret).now()
        )

        self.assertEqual(len(new_codes), mfa_service.RECOVERY_CODE_COUNT)
        self.assertFalse(set(new_codes) & set(old_codes))
        self.assertEqual(
            MFARecoveryCode.objects.filter(user=user, used_at__isnull=True).count(),
            mfa_service.RECOVERY_CODE_COUNT,
        )

    def test_disable_allowed_for_every_role(self):
        user = make_user(role=UserRole.ADMIN)
        secret, _codes = _enroll_and_confirm(user)

        mfa_service.disable(user=user, code=pyotp.TOTP(secret).now())

        self.assertIsNone(mfa_service.get_device(user=user))
        self.assertEqual(MFARecoveryCode.objects.filter(user=user).count(), 0)

    def test_admin_reset_deletes_device_and_recovery_codes(self):
        super_admin = make_user(role=UserRole.SUPER_ADMIN)
        target = make_user(role=UserRole.ADMIN)
        _enroll_and_confirm(target)

        mfa_service.admin_reset(acting_admin=super_admin, target_user=target)

        self.assertIsNone(mfa_service.get_device(user=target))
        self.assertEqual(MFARecoveryCode.objects.filter(user=target).count(), 0)
        self.assertTrue(
            UserActivityLog.objects.filter(
                user=target, action="MFA_RESET_BY_ADMIN"
            ).exists()
        )


@override_settings(MFA_ENFORCED=True)
class LoginMFAFlowApiTests(APITestCase):
    """Login/verify flow for an enrolled account under enforced MFA
    (production behaviour)."""
    def setUp(self):
        cache.clear()

    def _login(self, email, password="testpass123"):
        return self.client.post(
            "/api/v1/auth/login/",
            {"email": email, "password": password},
            format="json",
        )

    def test_non_mandated_role_logs_in_without_mfa(self):
        user = make_user(role=UserRole.COURSE_CREATOR)

        response = self._login(user.email)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)
        self.assertNotIn("mfa_required", response.data)

    def test_admin_with_device_gets_challenge_instead_of_tokens(self):
        user = make_user(role=UserRole.ADMIN)
        _enroll_and_confirm(user)

        response = self._login(user.email)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["mfa_required"])
        self.assertIn("challenge_token", response.data)
        self.assertNotIn("access", response.data)

    def test_full_login_then_verify_round_trip_issues_mfa_verified_token(self):
        user = make_user(role=UserRole.ADMIN)
        secret, _codes = _enroll_and_confirm(user)

        login_response = self._login(user.email)
        challenge_token = login_response.data["challenge_token"]

        verify_response = self.client.post(
            "/api/v1/auth/mfa/verify/",
            {"challenge_token": challenge_token, "code": pyotp.TOTP(secret).now()},
            format="json",
        )
        self.assertEqual(verify_response.status_code, status.HTTP_200_OK)
        access = AccessToken(verify_response.data["access"])
        self.assertTrue(access.get("mfa_verified"))
        self.assertEqual(
            access.get("sid"), RefreshToken(verify_response.data["refresh"])["sid"]
        )

    def test_verify_with_wrong_code_generic_error(self):
        user = make_user(role=UserRole.ADMIN)
        _enroll_and_confirm(user)
        login_response = self._login(user.email)
        challenge_token = login_response.data["challenge_token"]

        response = self.client.post(
            "/api/v1/auth/mfa/verify/",
            {"challenge_token": challenge_token, "code": "000000"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class MFAEnrollmentApiTests(APITestCase):
    def test_enroll_requires_authentication(self):
        response = self.client.post("/api/v1/auth/mfa/enroll/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_enroll_returns_secret_and_qr(self):
        user = make_user(role=UserRole.ADMIN)
        self.client.force_authenticate(user)

        response = self.client.post("/api/v1/auth/mfa/enroll/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("secret", response.data)
        self.assertIn("otpauth_uri", response.data)
        self.assertIn("qr_code_base64", response.data)

    def test_enroll_confirm_returns_recovery_codes_once(self):
        user = make_user(role=UserRole.ADMIN)
        self.client.force_authenticate(user)
        enroll_response = self.client.post("/api/v1/auth/mfa/enroll/")
        secret = enroll_response.data["secret"]

        response = self.client.post(
            "/api/v1/auth/mfa/enroll/confirm/",
            {"code": pyotp.TOTP(secret).now()},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            len(response.data["recovery_codes"]), mfa_service.RECOVERY_CODE_COUNT
        )


class MFAAdminResetApiTests(APITestCase):
    def test_requires_super_admin_role(self):
        admin = make_user(role=UserRole.ADMIN)
        target = make_user(role=UserRole.ADMIN)
        self.client.force_authenticate(admin)

        response = self.client.post(f"/api/v1/auth/mfa/admin-reset/{target.id}/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_super_admin_can_reset_another_users_mfa(self):
        super_admin = make_user(role=UserRole.SUPER_ADMIN)
        target = make_user(role=UserRole.ADMIN)
        _enroll_and_confirm(target)
        self.client.force_authenticate(super_admin)

        response = self.client.post(f"/api/v1/auth/mfa/admin-reset/{target.id}/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(mfa_service.get_device(user=target))


@override_settings(MFA_ENFORCED=True)
class AdminActionsNeedNoMFAApiTests(APITestCase):
    """Platform settings and category writes are gated by permission alone:
    an admin who never opted in to MFA is not blocked, even where MFA is
    enforced."""

    def test_unenrolled_admin_can_patch_platform_settings(self):
        admin = make_user(role=UserRole.ADMIN)
        token = AccessToken.for_user(admin)
        token["mfa_verified"] = False
        self.client.force_authenticate(admin, token=token)

        response = self.client.patch(
            "/api/v1/platform-settings/",
            {"course_module_count_min": 6},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["course_module_count_min"], 6)

    def test_course_creator_still_cannot_patch_platform_settings(self):
        self.client.force_authenticate(make_user(role=UserRole.COURSE_CREATOR))

        response = self.client.patch(
            "/api/v1/platform-settings/",
            {"course_module_count_min": 6},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_unenrolled_admin_can_create_a_category(self):
        self.client.force_authenticate(make_user(role=UserRole.ADMIN))

        response = self.client.post(
            "/api/v1/categories/",
            {
                "name": "Admin Category",
                "creator_price_beginner": "50.00",
                "creator_price_intermediate": "50.00",
                "creator_price_advanced": "50.00",
                "track_preference": "OPEN",
                "status": "ACTIVE",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)


class MFAEnforcementOffApiTests(APITestCase):
    """Non-enforced MFA (dev/staging behaviour, the default in tests):

    a mandated-role account (freshly bootstrapped super admin included)
    logs in with just email + password, gets `mfa_verified=true` on the
    token, and faces no challenge or enrollment flags. Accounts that have
    enrolled a device are still challenged.
    """

    def setUp(self):
        cache.clear()

    def _login(self, email, password="testpass123"):
        return self.client.post(
            "/api/v1/auth/login/",
            {"email": email, "password": password},
            format="json",
        )

    def test_super_admin_logs_in_without_mfa_when_not_enforced(self):
        user = make_user(role=UserRole.SUPER_ADMIN)

        response = self._login(user.email)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)
        self.assertNotIn("mfa_required", response.data)
        self.assertNotIn("mfa_enrollment_required", response.data)
        self.assertNotIn("mfa_enrollment_overdue", response.data)
        access = AccessToken(response.data["access"])
        self.assertTrue(access.get("mfa_verified"))

    def test_enrolled_user_still_gets_challenge_when_not_enforced(self):
        user = make_user(role=UserRole.COURSE_CREATOR)
        _enroll_and_confirm(user)

        response = self._login(user.email)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["mfa_required"])
        self.assertIn("challenge_token", response.data)
        self.assertNotIn("access", response.data)

    def test_enrolled_user_verify_round_trip_when_not_enforced(self):
        user = make_user(role=UserRole.ADMIN)
        secret, _codes = _enroll_and_confirm(user)
        challenge_token = self._login(user.email).data["challenge_token"]

        wrong = self.client.post(
            "/api/v1/auth/mfa/verify/",
            {"challenge_token": challenge_token, "code": "000000"},
            format="json",
        )
        self.assertEqual(wrong.status_code, status.HTTP_400_BAD_REQUEST)

        response = self.client.post(
            "/api/v1/auth/mfa/verify/",
            {"challenge_token": challenge_token, "code": pyotp.TOTP(secret).now()},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)

    def test_admin_without_mfa_claim_allowed_when_not_enforced(self):
        admin = make_user(role=UserRole.ADMIN)
        token = AccessToken.for_user(admin)
        token["mfa_verified"] = False
        self.client.force_authenticate(admin, token=token)

        response = self.client.patch(
            "/api/v1/platform-settings/",
            {"course_module_count_min": 6},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class MFAOptInApiTests(APITestCase):
    """MFA is opt-in for admins and super admins, in every environment: an
    account that never enrolled just logs in, one that enrolled is always
    challenged, and only the account holder can turn it on or off."""

    def setUp(self):
        cache.clear()

    def _login(self, email, password="testpass123"):
        return self.client.post(
            "/api/v1/auth/login/",
            {"email": email, "password": password},
            format="json",
        )

    def _assert_plain_login(self, user):
        response = self._login(user.email)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)
        for flag in (
            "mfa_required",
            "challenge_token",
            "mfa_enrollment_required",
            "mfa_enrollment_overdue",
        ):
            self.assertNotIn(flag, response.data)
        self.assertTrue(AccessToken(response.data["access"]).get("mfa_verified"))

    @override_settings(MFA_ENFORCED=True)
    def test_unenrolled_admin_and_super_admin_log_in_when_enforced(self):
        for role in (UserRole.ADMIN, UserRole.SUPER_ADMIN):
            with self.subTest(role=role):
                self._assert_plain_login(make_user(role=role))

    @override_settings(MFA_ENFORCED=False)
    def test_unenrolled_admin_and_super_admin_log_in_when_not_enforced(self):
        for role in (UserRole.ADMIN, UserRole.SUPER_ADMIN):
            with self.subTest(role=role):
                self._assert_plain_login(make_user(role=role))

    def test_admin_who_enrolled_is_challenged_whether_or_not_enforced(self):
        for enforced in (True, False):
            with self.subTest(enforced=enforced), override_settings(
                MFA_ENFORCED=enforced
            ):
                user = make_user(role=UserRole.ADMIN)
                _enroll_and_confirm(user)

                response = self._login(user.email)

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertTrue(response.data["mfa_required"])
                self.assertIn("challenge_token", response.data)
                self.assertNotIn("access", response.data)

    def test_admin_opts_in_over_http_then_is_challenged_at_login(self):
        user = make_user(role=UserRole.SUPER_ADMIN)
        self.client.force_authenticate(user)
        secret = self.client.post("/api/v1/auth/mfa/enroll/").data["secret"]
        confirm = self.client.post(
            "/api/v1/auth/mfa/enroll/confirm/",
            {"code": pyotp.TOTP(secret).now()},
            format="json",
        )
        self.assertEqual(confirm.status_code, status.HTTP_200_OK)
        self.client.force_authenticate(None)

        response = self._login(user.email)

        self.assertTrue(response.data["mfa_required"])

    def test_admin_can_opt_back_out_with_a_valid_code(self):
        user = make_user(role=UserRole.ADMIN)
        secret, _codes = _enroll_and_confirm(user)
        self.client.force_authenticate(user)

        response = self.client.post(
            "/api/v1/auth/mfa/disable/",
            {"code": pyotp.TOTP(secret).now()},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["detail"], "MFA disabled.")
        self.assertIsNone(mfa_service.get_device(user=user))
        self.client.force_authenticate(None)
        self._assert_plain_login(user)

    def test_admin_cannot_opt_out_with_a_wrong_code(self):
        user = make_user(role=UserRole.ADMIN)
        _enroll_and_confirm(user)
        self.client.force_authenticate(user)

        response = self.client.post(
            "/api/v1/auth/mfa/disable/", {"code": "000000"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(mfa_service.get_device(user=user).is_enabled)

    def test_disable_requires_authentication(self):
        response = self.client.post(
            "/api/v1/auth/mfa/disable/", {"code": "123456"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_disable_only_affects_the_caller(self):
        caller = make_user(role=UserRole.ADMIN)
        other = make_user(role=UserRole.ADMIN)
        caller_secret, _ = _enroll_and_confirm(caller)
        _enroll_and_confirm(other)
        self.client.force_authenticate(caller)

        self.client.post(
            "/api/v1/auth/mfa/disable/",
            {"code": pyotp.TOTP(caller_secret).now()},
            format="json",
        )

        self.assertIsNone(mfa_service.get_device(user=caller))
        self.assertTrue(mfa_service.get_device(user=other).is_enabled)


class MeMFAEnabledApiTests(APITestCase):
    """`mfa_enabled` on the profile object returned by /users/me/ and by the
    login / verify / refresh responses."""

    def setUp(self):
        cache.clear()

    def test_me_reports_false_for_an_account_that_never_enrolled(self):
        self.client.force_authenticate(make_user(role=UserRole.ADMIN))

        response = self.client.get("/api/v1/users/me/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIs(response.data["mfa_enabled"], False)

    def test_me_reports_false_while_enrollment_is_unconfirmed(self):
        user = make_user(role=UserRole.ADMIN)
        mfa_service.enroll(user=user)
        self.client.force_authenticate(user)

        response = self.client.get("/api/v1/users/me/")

        self.assertIs(response.data["mfa_enabled"], False)

    def test_me_reports_true_once_enrollment_is_confirmed(self):
        user = make_user(role=UserRole.SUPER_ADMIN)
        _enroll_and_confirm(user)
        self.client.force_authenticate(user)

        response = self.client.get("/api/v1/users/me/")

        self.assertIs(response.data["mfa_enabled"], True)

    def test_me_reports_false_again_after_opting_out(self):
        user = make_user(role=UserRole.ADMIN)
        secret, _codes = _enroll_and_confirm(user)
        self.client.force_authenticate(user)

        self.client.post(
            "/api/v1/auth/mfa/disable/",
            {"code": pyotp.TOTP(secret).now()},
            format="json",
        )

        self.assertIs(self.client.get("/api/v1/users/me/").data["mfa_enabled"], False)

    def test_plain_login_returns_mfa_enabled_false_in_user(self):
        user = make_user(role=UserRole.ADMIN)

        response = self.client.post(
            "/api/v1/auth/login/",
            {"email": user.email, "password": "testpass123"},
            format="json",
        )

        self.assertIs(response.data["user"]["mfa_enabled"], False)

    def test_verify_response_returns_mfa_enabled_true_in_user(self):
        user = make_user(role=UserRole.ADMIN)
        secret, _codes = _enroll_and_confirm(user)
        challenge = self.client.post(
            "/api/v1/auth/login/",
            {"email": user.email, "password": "testpass123"},
            format="json",
        ).data["challenge_token"]

        response = self.client.post(
            "/api/v1/auth/mfa/verify/",
            {"challenge_token": challenge, "code": pyotp.TOTP(secret).now()},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIs(response.data["user"]["mfa_enabled"], True)

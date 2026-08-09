import pyotp
from django.core.cache import cache
from django.test import TestCase
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

    def test_disable_forbidden_for_admin(self):
        user = make_user(role=UserRole.ADMIN)
        secret, _codes = _enroll_and_confirm(user)

        with self.assertRaises(Exception):
            mfa_service.disable(user=user, code=pyotp.TOTP(secret).now())
        self.assertTrue(mfa_service.get_device(user=user).is_enabled)

    def test_disable_allowed_for_non_mandated_role(self):
        user = make_user(role=UserRole.COURSE_CREATOR)
        secret, _codes = _enroll_and_confirm(user)

        mfa_service.disable(user=user, code=pyotp.TOTP(secret).now())

        self.assertIsNone(mfa_service.get_device(user=user))
        self.assertEqual(MFARecoveryCode.objects.filter(user=user).count(), 0)

    def test_admin_reset_deletes_device_without_granting_new_grace_period(self):
        super_admin = make_user(role=UserRole.SUPER_ADMIN)
        target = make_user(role=UserRole.ADMIN)
        _enroll_and_confirm(target)
        original_grace = target.mfa_grace_period_ends_at

        mfa_service.admin_reset(acting_admin=super_admin, target_user=target)

        self.assertIsNone(mfa_service.get_device(user=target))
        target.refresh_from_db()
        self.assertEqual(target.mfa_grace_period_ends_at, original_grace)
        self.assertTrue(
            UserActivityLog.objects.filter(
                user=target, action="MFA_RESET_BY_ADMIN"
            ).exists()
        )


class MFAGracePeriodTests(TestCase):
    def test_grace_period_set_on_first_save_with_mandated_role(self):
        user = make_user(role=UserRole.ADMIN)
        self.assertIsNotNone(user.mfa_grace_period_ends_at)
        self.assertGreater(user.mfa_grace_period_ends_at, timezone.now())

    def test_grace_period_not_reset_on_subsequent_saves(self):
        user = make_user(role=UserRole.ADMIN)
        first = user.mfa_grace_period_ends_at

        user.first_name = "Changed"
        user.save(update_fields=["first_name"])
        user.refresh_from_db()

        self.assertEqual(user.mfa_grace_period_ends_at, first)

    def test_non_mandated_role_never_gets_a_grace_period(self):
        user = make_user(role=UserRole.COURSE_CREATOR)
        self.assertIsNone(user.mfa_grace_period_ends_at)


class LoginMFAFlowApiTests(APITestCase):
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

    def test_admin_without_device_logs_in_flagged_within_grace_period(self):
        user = make_user(role=UserRole.ADMIN)

        response = self._login(user.email)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)
        self.assertTrue(response.data["mfa_enrollment_required"])
        access = AccessToken(response.data["access"])
        self.assertFalse(access.get("mfa_verified"))

    def test_admin_without_device_past_grace_period_still_logs_in_but_unverified(self):
        user = make_user(role=UserRole.ADMIN)
        from api.users.models import User

        User.objects.filter(id=user.id).update(
            mfa_grace_period_ends_at=timezone.now() - timezone.timedelta(days=1)
        )

        response = self._login(user.email)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["mfa_enrollment_overdue"])
        access = AccessToken(response.data["access"])
        self.assertFalse(access.get("mfa_verified"))

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
        self.assertEqual(access.get("sid"), RefreshToken(verify_response.data["refresh"])["sid"])

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
        self.assertEqual(len(response.data["recovery_codes"]), mfa_service.RECOVERY_CODE_COUNT)


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


class IsMFAVerifiedForSessionApiTests(APITestCase):
    def _token(self, user, mfa_verified):
        token = AccessToken.for_user(user)
        token["mfa_verified"] = mfa_verified
        return token

    def test_admin_without_mfa_verified_claim_blocked_from_platform_settings_patch(self):
        admin = make_user(role=UserRole.ADMIN)
        self.client.force_authenticate(admin, token=self._token(admin, False))

        response = self.client.patch(
            "/api/v1/platform-settings/",
            {"course_module_count_min": 6},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_with_mfa_verified_claim_allowed(self):
        admin = make_user(role=UserRole.ADMIN)
        self.client.force_authenticate(admin, token=self._token(admin, True))

        response = self.client.patch(
            "/api/v1/platform-settings/",
            {"course_module_count_min": 6},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_non_mandated_role_unaffected_by_claim(self):
        writer = make_user(role=UserRole.STAFF_WRITER)
        # No token at all - non-mandated roles short-circuit past the claim
        # check entirely in IsMFAVerifiedForSession.
        self.client.force_authenticate(writer)

        response = self.client.post(
            "/api/v1/categories/",
            {
                "name": "Writer Category",
                "creator_price": "50.00",
                "track_preference": "OPEN",
                "status": "ACTIVE",
            },
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

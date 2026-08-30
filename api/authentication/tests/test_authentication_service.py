from unittest.mock import patch

from django.conf import settings
from django.core import mail
from django.test import TestCase
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
from rest_framework_simplejwt.tokens import RefreshToken

from api.authentication.enums import TokenPurpose
from api.authentication.services.authentication_service import AuthenticationService
from api.authentication.tests.factories import make_user, make_verification_token
from api.users.enums import UserRole
from api.users.models import UserActivityLog

service = AuthenticationService()


class SignupTests(TestCase):
    def test_creates_inactive_user_with_forced_role(self):
        user = service.signup(
            email="new@example.com",
            password="StrongPass123!",
            first_name="A",
            last_name="B",
            country="NG",
        )

        self.assertFalse(user.is_active)
        self.assertEqual(user.role, UserRole.COURSE_CREATOR)
        self.assertEqual(user.country, "NG")
        self.assertIsNone(user.terms_accepted_at)

    def test_terms_accepted_true_stamps_timestamp(self):
        user = service.signup(
            email="terms@example.com",
            password="StrongPass123!",
            first_name="A",
            last_name="B",
            country="NG",
            terms_accepted=True,
        )

        self.assertIsNotNone(user.terms_accepted_at)

    def test_sends_verification_email_with_link(self):
        # signup() emails via transaction.on_commit(), which never fires under
        # TestCase's rolled-back outer transaction unless captured like this.
        with self.captureOnCommitCallbacks(execute=True):
            service.signup(
                email="new2@example.com",
                password="StrongPass123!",
                first_name="A",
                last_name="B",
                country="NG",
            )
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("new2@example.com", mail.outbox[0].to)
        self.assertIn(f"{settings.FRONTEND_URL}/verify-email", mail.outbox[0].body)
        self.assertIn("token=", mail.outbox[0].body)

    @patch("api.notification.services.email_service.send_templated_email")
    def test_successful_send_is_logged(self, mock_send):
        mock_send.return_value = 1
        with self.assertLogs(
            "api.authentication.services.authentication_service", level="INFO"
        ) as logs:
            with self.captureOnCommitCallbacks(execute=True):
                service.signup(
                    email="logged@example.com",
                    password="StrongPass123!",
                    first_name="Log",
                    last_name="Test",
                    country="NG",
                )

        mock_send.assert_called_once()
        self.assertTrue(
            any(
                "auth_email_sent email_type=SIGNUP_VERIFICATION" in message
                and "logged@example.com" in message
                for message in logs.output
            )
        )

    def test_raises_on_duplicate_email(self):
        make_user(email="dupe@example.com")
        with self.assertRaises(ValidationError):
            service.signup(
                email="dupe@example.com",
                password="StrongPass123!",
                first_name="A",
                last_name="B",
                country="NG",
            )


class VerifyEmailTests(TestCase):
    def test_activates_user_and_logs_activity(self):
        user = make_user(is_active=False)
        make_verification_token(
            user=user, purpose=TokenPurpose.SIGNUP_VERIFICATION, raw_token="verify-me"
        )

        result = service.verify_otp(email=user.email, token="verify-me")

        self.assertTrue(result.is_active)
        self.assertTrue(
            UserActivityLog.objects.filter(
                user=user, action="ACCOUNT_VERIFIED"
            ).exists()
        )

    def test_raises_not_found_for_unknown_email(self):
        with self.assertRaises(NotFound):
            service.verify_otp(email="ghost@example.com", token="verify-me")


class ResetPasswordTests(TestCase):
    def test_happy_path_changes_password_and_logs(self):
        user = make_user()
        old_password_hash = user.password
        make_verification_token(
            user=user, purpose=TokenPurpose.PASSWORD_RESET, raw_token="reset-me"
        )

        service.reset_password(
            email=user.email, token="reset-me", new_password="NewStrongPass456!"
        )

        user.refresh_from_db()
        self.assertNotEqual(user.password, old_password_hash)
        self.assertTrue(user.check_password("NewStrongPass456!"))
        self.assertTrue(
            UserActivityLog.objects.filter(
                user=user, action="PASSWORD_RESET_COMPLETED"
            ).exists()
        )

    def test_revokes_existing_sessions_and_sends_confirmation(self):
        user = make_user()
        refresh = RefreshToken.for_user(user)
        make_verification_token(
            user=user, purpose=TokenPurpose.PASSWORD_RESET, raw_token="reset-me"
        )

        service.reset_password(
            email=user.email, token="reset-me", new_password="NewStrongPass456!"
        )

        self.assertTrue(
            BlacklistedToken.objects.filter(token__jti=refresh["jti"]).exists()
        )
        self.assertTrue(
            UserActivityLog.objects.filter(
                user=user, action="SESSIONS_REVOKED"
            ).exists()
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "Your password was changed")
        self.assertIn(user.email, mail.outbox[0].to)

    def test_does_not_revoke_other_users_sessions(self):
        user = make_user()
        other_user = make_user()
        other_refresh = RefreshToken.for_user(other_user)
        make_verification_token(
            user=user, purpose=TokenPurpose.PASSWORD_RESET, raw_token="reset-me"
        )

        service.reset_password(
            email=user.email, token="reset-me", new_password="NewStrongPass456!"
        )

        self.assertFalse(
            BlacklistedToken.objects.filter(token__jti=other_refresh["jti"]).exists()
        )

    def test_raises_with_expired_token(self):
        user = make_user()
        make_verification_token(
            user=user,
            purpose=TokenPurpose.PASSWORD_RESET,
            raw_token="reset-me",
            expires_in_minutes=-1,
        )

        with self.assertRaises(ValidationError):
            service.reset_password(
                email=user.email, token="reset-me", new_password="NewStrongPass456!"
            )

    def test_raises_with_already_used_token(self):
        user = make_user()
        make_verification_token(
            user=user,
            purpose=TokenPurpose.PASSWORD_RESET,
            raw_token="reset-me",
            is_used=True,
        )

        with self.assertRaises(NotFound):
            service.reset_password(
                email=user.email, token="reset-me", new_password="NewStrongPass456!"
            )


class LogoutTests(TestCase):
    def test_blacklists_refresh_token_and_logs(self):
        user = make_user()
        refresh = RefreshToken.for_user(user)

        service.logout(user=user, refresh_token=str(refresh))

        self.assertTrue(
            BlacklistedToken.objects.filter(token__jti=refresh["jti"]).exists()
        )
        self.assertTrue(
            UserActivityLog.objects.filter(user=user, action="LOGOUT").exists()
        )

    def test_raises_on_garbage_token(self):
        user = make_user()
        with self.assertRaises(ValidationError):
            service.logout(user=user, refresh_token="not-a-real-token")


class ForgotPasswordTests(TestCase):
    def test_sends_email_with_link_for_existing_user(self):
        user = make_user()
        service.forgot_password(email=user.email)

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(f"{settings.FRONTEND_URL}/reset-password", mail.outbox[0].body)
        self.assertIn(f"email={user.email}", mail.outbox[0].body.replace("%40", "@"))

    def test_silent_and_no_email_for_nonexistent_user(self):
        service.forgot_password(email="ghost@example.com")
        self.assertEqual(len(mail.outbox), 0)

    def test_existing_user_sends_password_reset_email(self):
        user = make_user()

        service.forgot_password(email=user.email)

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Reset your password", mail.outbox[0].subject)
        self.assertIn("reset-password", mail.outbox[0].body)

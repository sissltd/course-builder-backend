from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from api.authentication.enums import TokenPurpose
from api.authentication.models import EmailVerificationToken
from api.authentication.services import token_service
from api.authentication.tests.factories import make_user, make_verification_token


class IssueTokenTests(TestCase):
    def test_creates_hashed_token_and_returns_raw_token(self):
        user = make_user()
        record, raw_token = token_service.issue_token(user=user, purpose=TokenPurpose.SIGNUP_VERIFICATION)

        self.assertNotEqual(record.token_hash, raw_token)
        self.assertGreater(len(raw_token), 20)
        self.assertFalse(record.is_used)

    def test_invalidates_prior_unused_token_for_same_user_and_purpose(self):
        user = make_user()
        first, _ = token_service.issue_token(user=user, purpose=TokenPurpose.SIGNUP_VERIFICATION)
        second, _ = token_service.issue_token(user=user, purpose=TokenPurpose.SIGNUP_VERIFICATION)

        first.refresh_from_db()
        self.assertTrue(first.is_used)
        self.assertFalse(second.is_used)


class VerifyTokenTests(TestCase):
    def test_succeeds_with_correct_token(self):
        user = make_user()
        record = make_verification_token(
            user=user, purpose=TokenPurpose.SIGNUP_VERIFICATION, raw_token="correct-token"
        )

        result = token_service.verify_token(
            user=user, purpose=TokenPurpose.SIGNUP_VERIFICATION, token="correct-token"
        )
        self.assertEqual(result.id, record.id)
        result.refresh_from_db()
        self.assertTrue(result.is_used)

    def test_raises_not_found_when_no_active_token(self):
        user = make_user()
        with self.assertRaises(NotFound):
            token_service.verify_token(
                user=user, purpose=TokenPurpose.SIGNUP_VERIFICATION, token="nonexistent"
            )

    def test_raises_not_found_on_wrong_token(self):
        user = make_user()
        make_verification_token(
            user=user, purpose=TokenPurpose.SIGNUP_VERIFICATION, raw_token="correct-token"
        )

        with self.assertRaises(NotFound):
            token_service.verify_token(
                user=user, purpose=TokenPurpose.SIGNUP_VERIFICATION, token="wrong-token"
            )

    def test_raises_not_found_when_token_belongs_to_different_user(self):
        user = make_user()
        other_user = make_user()
        make_verification_token(
            user=user, purpose=TokenPurpose.SIGNUP_VERIFICATION, raw_token="correct-token"
        )

        with self.assertRaises(NotFound):
            token_service.verify_token(
                user=other_user, purpose=TokenPurpose.SIGNUP_VERIFICATION, token="correct-token"
            )

    def test_raises_not_found_when_purpose_mismatches(self):
        user = make_user()
        make_verification_token(
            user=user, purpose=TokenPurpose.SIGNUP_VERIFICATION, raw_token="correct-token"
        )

        with self.assertRaises(NotFound):
            token_service.verify_token(
                user=user, purpose=TokenPurpose.PASSWORD_RESET, token="correct-token"
            )

    def test_raises_on_expired_token(self):
        user = make_user()
        make_verification_token(
            user=user,
            purpose=TokenPurpose.SIGNUP_VERIFICATION,
            raw_token="correct-token",
            expires_in_minutes=-1,
        )

        with self.assertRaises(ValidationError):
            token_service.verify_token(
                user=user, purpose=TokenPurpose.SIGNUP_VERIFICATION, token="correct-token"
            )

    def test_raises_once_attempts_exceeded(self):
        user = make_user()
        make_verification_token(
            user=user, purpose=TokenPurpose.SIGNUP_VERIFICATION, raw_token="correct-token", attempts=5
        )

        with self.assertRaises(ValidationError):
            token_service.verify_token(
                user=user, purpose=TokenPurpose.SIGNUP_VERIFICATION, token="correct-token"
            )


class CanResendTests(TestCase):
    def test_true_when_no_active_token(self):
        user = make_user()
        self.assertTrue(token_service.can_resend(user=user, purpose=TokenPurpose.SIGNUP_VERIFICATION))

    @override_settings(EMAIL_TOKEN_RESEND_COOLDOWN_SECONDS=60)
    def test_false_immediately_after_issuance(self):
        user = make_user()
        token_service.issue_token(user=user, purpose=TokenPurpose.SIGNUP_VERIFICATION)
        self.assertFalse(token_service.can_resend(user=user, purpose=TokenPurpose.SIGNUP_VERIFICATION))

    @override_settings(EMAIL_TOKEN_RESEND_COOLDOWN_SECONDS=60)
    def test_true_after_cooldown_elapses(self):
        user = make_user()
        record, _ = token_service.issue_token(user=user, purpose=TokenPurpose.SIGNUP_VERIFICATION)
        EmailVerificationToken.objects.filter(pk=record.pk).update(
            created_datetime=timezone.now() - timedelta(seconds=61)
        )

        self.assertTrue(token_service.can_resend(user=user, purpose=TokenPurpose.SIGNUP_VERIFICATION))

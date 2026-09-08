from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from api.authentication.enums import ExternalIdentityProvider, TokenPurpose
from api.authentication.models import ExternalIdentity, UserSession
from api.authentication.tests.factories import make_user, make_verification_token
from api.users.enums import AccountStatus, UserActivityActionEnums, UserRole
from api.users.models import User, UserActivityLog

GOOGLE_VERIFY_PATH = (
    "api.authentication.services.google_auth_service."
    "google_id_token.verify_oauth2_token"
)


@override_settings(GOOGLE_OAUTH_CLIENT_IDS=("web-client-id", "mobile-client-id"))
class GoogleAuthenticationApiTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.verify_patcher = patch(GOOGLE_VERIFY_PATH)
        self.verify_token = self.verify_patcher.start()
        self.addCleanup(self.verify_patcher.stop)
        self.set_google_identity()

    def set_google_identity(
        self,
        *,
        subject="google-subject-1",
        email="creator@example.com",
        audience="web-client-id",
        email_verified=True,
    ):
        self.verify_token.return_value = {
            "sub": subject,
            "email": email,
            "email_verified": email_verified,
            "aud": audience,
            "iss": "https://accounts.google.com",
        }

    def signup_payload(self, **overrides):
        payload = {
            "id_token": "signed-google-id-token",
            "first_name": "Ada",
            "last_name": "Lovelace",
            "country": "ng",
            "terms_accepted": True,
        }
        payload.update(overrides)
        return payload

    def test_creator_signup_creates_active_passwordless_account_and_session(self):
        response = self.client.post(
            "/api/v1/auth/signup/google/", self.signup_payload(), format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            set(response.data), {"access", "refresh", "user", "role", "workspace"}
        )
        self.assertEqual(response.data["role"], UserRole.COURSE_CREATOR)
        self.assertEqual(response.data["workspace"], "creator_studio")
        self.assertFalse(response.data["user"]["has_completed_onboarding"])

        user = User.objects.get(email="creator@example.com")
        self.assertTrue(user.is_active)
        self.assertEqual(user.status, AccountStatus.ACTIVE)
        self.assertEqual(user.country, "NG")
        self.assertFalse(user.has_usable_password())
        self.assertIsNotNone(user.terms_accepted_at)
        self.assertIsNotNone(user.last_login)
        self.assertEqual(mail.outbox, [])
        self.assertTrue(UserSession.objects.filter(user=user).exists())

        identity = ExternalIdentity.objects.get(user=user)
        self.assertEqual(identity.provider, ExternalIdentityProvider.GOOGLE)
        self.assertEqual(identity.subject, "google-subject-1")
        self.assertEqual(identity.email, "creator@example.com")
        self.assertTrue(
            UserActivityLog.objects.filter(
                user=user,
                action=UserActivityActionEnums.GOOGLE_IDENTITY_LINKED,
            ).exists()
        )

    def test_reviewer_signup_forces_reviewer_role_and_workspace(self):
        self.set_google_identity(email="reviewer@example.com")

        response = self.client.post(
            "/api/v1/auth/reviewer/signup/google/",
            self.signup_payload(),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["role"], UserRole.CREATOR_REVIEWER)
        self.assertEqual(response.data["workspace"], "creator_review_dashboard")
        self.assertEqual(
            User.objects.get(email="reviewer@example.com").role,
            UserRole.CREATOR_REVIEWER,
        )

    def test_creator_login_links_existing_password_account(self):
        user = make_user(
            email="creator@example.com",
            role=UserRole.COURSE_CREATOR,
            status=AccountStatus.ACTIVE,
            failed_login_attempts=3,
        )

        response = self.client.post(
            "/api/v1/auth/login/google/",
            {"id_token": "signed-google-id-token"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["user"]["id"], str(user.id))
        self.assertEqual(response.data["role"], UserRole.COURSE_CREATOR)
        user.refresh_from_db()
        self.assertEqual(user.failed_login_attempts, 0)
        self.assertIsNotNone(user.last_login)
        self.assertTrue(ExternalIdentity.objects.filter(user=user).exists())

    def test_reviewer_login_alias_uses_the_same_google_flow(self):
        self.set_google_identity(email="reviewer@example.com")
        user = make_user(
            email="reviewer@example.com",
            role=UserRole.CREATOR_REVIEWER,
            status=AccountStatus.ACTIVE,
        )

        response = self.client.post(
            "/api/v1/auth/reviewer/login/google/",
            {"id_token": "signed-google-id-token"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["user"]["id"], str(user.id))
        self.assertEqual(response.data["workspace"], "creator_review_dashboard")

    def test_signup_with_existing_email_links_and_preserves_role_and_profile(self):
        user = make_user(
            email="creator@example.com",
            first_name="Existing",
            last_name="Creator",
            country="GH",
            role=UserRole.COURSE_CREATOR,
            status=AccountStatus.ACTIVE,
        )

        response = self.client.post(
            "/api/v1/auth/reviewer/signup/google/",
            self.signup_payload(first_name="Replacement", country="NG"),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["role"], UserRole.COURSE_CREATOR)
        user.refresh_from_db()
        self.assertEqual(user.first_name, "Existing")
        self.assertEqual(user.country, "GH")
        self.assertIsNotNone(user.terms_accepted_at)

    def test_linking_pending_account_activates_it_and_invalidates_email_token(self):
        user = make_user(
            email="creator@example.com",
            role=UserRole.COURSE_CREATOR,
            is_active=False,
            status=AccountStatus.PENDING_VERIFICATION,
        )
        verification_token = make_verification_token(
            user=user, purpose=TokenPurpose.SIGNUP_VERIFICATION
        )

        response = self.client.post(
            "/api/v1/auth/login/google/",
            {"id_token": "signed-google-id-token"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        user.refresh_from_db()
        verification_token.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertEqual(user.status, AccountStatus.ACTIVE)
        self.assertTrue(verification_token.is_used)

    def test_repeated_google_login_reuses_identity(self):
        user = make_user(
            email="creator@example.com",
            role=UserRole.COURSE_CREATOR,
            status=AccountStatus.ACTIVE,
        )
        url = "/api/v1/auth/login/google/"
        payload = {"id_token": "signed-google-id-token"}

        first_response = self.client.post(url, payload, format="json")
        second_response = self.client.post(url, payload, format="json")

        self.assertEqual(first_response.status_code, status.HTTP_200_OK)
        self.assertEqual(second_response.status_code, status.HTTP_200_OK)
        self.assertEqual(ExternalIdentity.objects.filter(user=user).count(), 1)
        self.assertEqual(UserSession.objects.filter(user=user).count(), 2)

    def test_google_login_does_not_create_unknown_account(self):
        response = self.client.post(
            "/api/v1/auth/login/google/",
            {"id_token": "signed-google-id-token"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(User.objects.filter(email="creator@example.com").exists())
        self.assertIn("sign up first", response.data["errors"][0]["message"].lower())

    def test_signup_requires_terms_acceptance(self):
        response = self.client.post(
            "/api/v1/auth/signup/google/",
            self.signup_payload(terms_accepted=False),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["errors"][0]["field_name"], "terms_accepted")
        self.verify_token.assert_not_called()

    def test_provider_signature_expiry_and_issuer_failures_are_rejected(self):
        for provider_error in ("bad signature", "expired token", "wrong issuer"):
            with self.subTest(provider_error=provider_error):
                self.verify_token.side_effect = ValueError(provider_error)
                response = self.client.post(
                    "/api/v1/auth/login/google/",
                    {"id_token": "tampered-token"},
                    format="json",
                )

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(response.data["errors"][0]["field_name"], "id_token")
                self.assertEqual(
                    response.data["errors"][0]["message"],
                    "Invalid Google credential.",
                )

    def test_token_for_unconfigured_audience_is_rejected(self):
        self.set_google_identity(audience="attacker-client-id")

        response = self.client.post(
            "/api/v1/auth/login/google/",
            {"id_token": "signed-google-id-token"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(ExternalIdentity.objects.exists())

    def test_unverified_google_email_is_rejected(self):
        self.set_google_identity(email_verified=False)

        response = self.client.post(
            "/api/v1/auth/signup/google/", self.signup_payload(), format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(User.objects.filter(email="creator@example.com").exists())

    def test_missing_required_google_claim_is_rejected(self):
        self.verify_token.return_value = {
            "email": "creator@example.com",
            "email_verified": True,
            "aud": "web-client-id",
        }

        response = self.client.post(
            "/api/v1/auth/signup/google/", self.signup_payload(), format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(User.objects.filter(email="creator@example.com").exists())

    def test_token_for_secondary_configured_audience_is_accepted(self):
        self.set_google_identity(audience="mobile-client-id")

        response = self.client.post(
            "/api/v1/auth/signup/google/", self.signup_payload(), format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    @override_settings(GOOGLE_OAUTH_CLIENT_IDS=())
    def test_missing_google_configuration_returns_service_unavailable(self):
        self.client.raise_request_exception = False
        response = self.client.post(
            "/api/v1/auth/login/google/",
            {"id_token": "signed-google-id-token"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(response.data["errors"][0]["code"], "google_auth_unavailable")
        self.verify_token.assert_not_called()

    def test_staff_account_cannot_link_google(self):
        make_user(
            email="creator@example.com",
            role=UserRole.STAFF_WRITER,
            status=AccountStatus.ACTIVE,
        )

        response = self.client.post(
            "/api/v1/auth/login/google/",
            {"id_token": "signed-google-id-token"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(ExternalIdentity.objects.exists())

    def test_suspended_account_cannot_log_in_with_google(self):
        user = make_user(
            email="creator@example.com",
            role=UserRole.COURSE_CREATOR,
            status=AccountStatus.SUSPENDED,
        )

        response = self.client.post(
            "/api/v1/auth/login/google/",
            {"id_token": "signed-google-id-token"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(ExternalIdentity.objects.filter(user=user).exists())

    def test_deactivated_account_cannot_log_in_with_google(self):
        user = make_user(
            email="creator@example.com",
            role=UserRole.COURSE_CREATOR,
            status=AccountStatus.DEACTIVATED,
        )

        response = self.client.post(
            "/api/v1/auth/login/google/",
            {"id_token": "signed-google-id-token"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(ExternalIdentity.objects.filter(user=user).exists())

    def test_current_account_lock_blocks_google_login(self):
        user = make_user(
            email="creator@example.com",
            role=UserRole.COURSE_CREATOR,
            status=AccountStatus.ACTIVE,
            failed_login_attempts=5,
            locked_until=timezone.now() + timedelta(minutes=10),
        )

        response = self.client.post(
            "/api/v1/auth/login/google/",
            {"id_token": "signed-google-id-token"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Too many failed attempts", response.data["errors"][0]["message"])
        self.assertFalse(ExternalIdentity.objects.filter(user=user).exists())

    def test_expired_account_lock_is_cleared_by_google_login(self):
        user = make_user(
            email="creator@example.com",
            role=UserRole.COURSE_CREATOR,
            status=AccountStatus.ACTIVE,
            failed_login_attempts=5,
            locked_until=timezone.now() - timedelta(minutes=1),
        )

        response = self.client.post(
            "/api/v1/auth/login/google/",
            {"id_token": "signed-google-id-token"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        user.refresh_from_db()
        self.assertEqual(user.failed_login_attempts, 0)
        self.assertIsNone(user.locked_until)

    def test_existing_google_identity_with_different_subject_is_rejected(self):
        user = make_user(
            email="creator@example.com",
            role=UserRole.COURSE_CREATOR,
            status=AccountStatus.ACTIVE,
        )
        ExternalIdentity.objects.create(
            user=user,
            provider=ExternalIdentityProvider.GOOGLE,
            subject="original-subject",
            email=user.email,
        )

        response = self.client.post(
            "/api/v1/auth/login/google/",
            {"id_token": "signed-google-id-token"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(ExternalIdentity.objects.filter(user=user).count(), 1)

    def test_linked_subject_remains_authoritative_when_google_email_changes(self):
        user = make_user(
            email="original@example.com",
            role=UserRole.COURSE_CREATOR,
            status=AccountStatus.ACTIVE,
        )
        ExternalIdentity.objects.create(
            user=user,
            provider=ExternalIdentityProvider.GOOGLE,
            subject="google-subject-1",
            email=user.email,
        )
        self.set_google_identity(email="updated-by-google@example.com")

        response = self.client.post(
            "/api/v1/auth/login/google/",
            {"id_token": "signed-google-id-token"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["user"]["id"], str(user.id))
        self.assertFalse(
            User.objects.filter(email="updated-by-google@example.com").exists()
        )

    def test_provider_verifier_receives_the_complete_audience_allowlist(self):
        make_user(
            email="creator@example.com",
            role=UserRole.COURSE_CREATOR,
            status=AccountStatus.ACTIVE,
        )

        self.client.post(
            "/api/v1/auth/login/google/",
            {"id_token": "signed-google-id-token"},
            format="json",
        )

        args, kwargs = self.verify_token.call_args
        self.assertEqual(args[0], "signed-google-id-token")
        self.assertEqual(kwargs["audience"], ["web-client-id", "mobile-client-id"])
        self.assertEqual(settings.GOOGLE_OAUTH_CLIENT_IDS[0], "web-client-id")

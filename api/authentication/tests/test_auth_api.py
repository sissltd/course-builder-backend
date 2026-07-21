from datetime import timedelta

from django.conf import settings
from django.core import mail
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
from rest_framework_simplejwt.tokens import RefreshToken

from api.authentication.enums import TokenPurpose
from api.authentication.models import EmailVerificationToken
from api.authentication.tests.factories import make_user, make_verification_token
from api.users.enums import UserRole
from api.users.models import UserActivityLog


class SignupApiTests(APITestCase):
    def test_happy_path(self):
        # signup() emails via transaction.on_commit(), which never fires under
        # TestCase's rolled-back outer transaction unless captured like this.
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                "/api/v1/auth/signup/",
                {
                    "email": "creator@example.com",
                    "password": "StrongPass123!",
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

    def test_duplicate_email_rejected(self):
        make_user(email="dupe@example.com")
        response = self.client.post(
            "/api/v1/auth/signup/",
            {
                "email": "dupe@example.com",
                "password": "StrongPass123!",
                "first_name": "A",
                "last_name": "B",
                "country": "NG",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_creates_course_creator_role_by_default(self):
        response = self.client.post(
            "/api/v1/auth/signup/",
            {
                "email": "defaultrole@example.com",
                "password": "StrongPass123!",
                "first_name": "A",
                "last_name": "B",
                "country": "NG",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["role"], "COURSE_CREATOR")

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
                    "first_name": "A",
                    "last_name": "B",
                    "country": "NG",
                },
                format="json",
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(response.data["terms_accepted_at"])


class ReviewerSignupApiTests(APITestCase):
    def test_happy_path_creates_creator_reviewer_role(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                "/api/v1/auth/reviewer/signup/",
                {
                    "email": "reviewer@example.com",
                    "password": "StrongPass123!",
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
        self.assertTrue(
            any(error["field_name"] == "password" for error in response.data["errors"])
        )

    def test_nonexistent_email_rejected(self):
        response = self.client.post(
            "/api/v1/auth/login/",
            {"email": "nobody@example.com", "password": "WhateverPass1!"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(
            any(error["field_name"] == "email" for error in response.data["errors"])
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

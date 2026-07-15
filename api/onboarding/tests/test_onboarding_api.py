from rest_framework import status
from rest_framework.test import APITestCase

from api.onboarding.enums import ExpertiseArea
from api.onboarding.tests.factories import make_user


class OnboardingApiTests(APITestCase):
    def test_get_requires_authentication(self):
        response = self.client.get("/api/v1/users/me/onboarding/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_get_lazily_creates_empty_profile(self):
        user = make_user()
        self.client.force_authenticate(user)

        response = self.client.get("/api/v1/users/me/onboarding/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["primary_expertise_area"], "")
        self.assertFalse(response.data["has_completed_onboarding"])

    def test_patch_with_invalid_expertise_area_rejected(self):
        user = make_user()
        self.client.force_authenticate(user)

        response = self.client.patch(
            "/api/v1/users/me/onboarding/",
            {"expertise_area": "NOT_A_REAL_CHOICE"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_patch_with_only_expertise_area_leaves_other_fields_null(self):
        user = make_user()
        self.client.force_authenticate(user)

        response = self.client.patch(
            "/api/v1/users/me/onboarding/",
            {"expertise_area": ExpertiseArea.WEB_DEVELOPMENT},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["primary_expertise_area"], ExpertiseArea.WEB_DEVELOPMENT
        )
        self.assertEqual(response.data["video_comfort_level"], "")
        self.assertFalse(response.data["has_completed_onboarding"])

    def test_patch_others_without_specify_text_rejected(self):
        user = make_user()
        self.client.force_authenticate(user)

        response = self.client.patch(
            "/api/v1/users/me/onboarding/",
            {"expertise_area": ExpertiseArea.OTHERS},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_patch_others_with_specify_text_accepted(self):
        user = make_user()
        self.client.force_authenticate(user)

        response = self.client.patch(
            "/api/v1/users/me/onboarding/",
            {
                "expertise_area": ExpertiseArea.OTHERS,
                "other_expertise": "Podcast production",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["primary_expertise_area"], ExpertiseArea.OTHERS
        )
        self.assertEqual(response.data["primary_expertise_other"], "Podcast production")

    def test_patch_empty_body_rejected(self):
        user = make_user()
        self.client.force_authenticate(user)

        response = self.client.patch("/api/v1/users/me/onboarding/", {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_patch_agreement_accepted_completes_onboarding_and_issues_tokens(self):
        user = make_user()
        self.client.force_authenticate(user)

        response = self.client.patch(
            "/api/v1/users/me/onboarding/", {"agreement_accepted": True}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(response.data["onboarding_completed_at"])
        self.assertTrue(response.data["has_completed_onboarding"])
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)

    def test_patch_partial_step_does_not_issue_tokens(self):
        user = make_user()
        self.client.force_authenticate(user)

        response = self.client.patch(
            "/api/v1/users/me/onboarding/",
            {"video_comfort_level": "VERY_COMFORTABLE"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn("access", response.data)
        self.assertNotIn("refresh", response.data)


class MeOnboardingStatusApiTests(APITestCase):
    def test_has_completed_onboarding_flips_after_agreement_step(self):
        user = make_user()
        self.client.force_authenticate(user)

        before = self.client.get("/api/v1/users/me/")
        self.assertFalse(before.data["has_completed_onboarding"])

        self.client.patch(
            "/api/v1/users/me/onboarding/", {"agreement_accepted": True}, format="json"
        )

        after = self.client.get("/api/v1/users/me/")
        self.assertTrue(after.data["has_completed_onboarding"])

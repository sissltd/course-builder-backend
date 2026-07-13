from rest_framework import status
from rest_framework.test import APITestCase

from api.courses.enums import CategoryStatus
from api.onboarding.tests.factories import make_category, make_user


class OnboardingApiTests(APITestCase):
    def test_get_requires_authentication(self):
        response = self.client.get("/api/v1/users/me/onboarding/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_get_lazily_creates_empty_profile(self):
        user = make_user()
        self.client.force_authenticate(user)

        response = self.client.get("/api/v1/users/me/onboarding/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["primary_expertise_category"])
        self.assertFalse(response.data["has_completed_onboarding"])

    def test_patch_with_inactive_category_rejected(self):
        user = make_user()
        category = make_category(status=CategoryStatus.INACTIVE)
        self.client.force_authenticate(user)

        response = self.client.patch(
            "/api/v1/users/me/onboarding/", {"category_id": str(category.id)}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_patch_with_only_category_leaves_other_fields_null(self):
        user = make_user()
        category = make_category()
        self.client.force_authenticate(user)

        response = self.client.patch(
            "/api/v1/users/me/onboarding/", {"category_id": str(category.id)}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["primary_expertise_category"]["id"], str(category.id))
        self.assertEqual(response.data["video_comfort_level"], "")
        self.assertFalse(response.data["has_completed_onboarding"])

    def test_patch_empty_body_rejected(self):
        user = make_user()
        self.client.force_authenticate(user)

        response = self.client.patch("/api/v1/users/me/onboarding/", {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_patch_agreement_accepted_completes_onboarding(self):
        user = make_user()
        self.client.force_authenticate(user)

        response = self.client.patch(
            "/api/v1/users/me/onboarding/", {"agreement_accepted": True}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(response.data["onboarding_completed_at"])
        self.assertTrue(response.data["has_completed_onboarding"])


class MeOnboardingStatusApiTests(APITestCase):
    def test_has_completed_onboarding_flips_after_agreement_step(self):
        user = make_user()
        self.client.force_authenticate(user)

        before = self.client.get("/api/v1/users/me/")
        self.assertFalse(before.data["has_completed_onboarding"])

        self.client.patch("/api/v1/users/me/onboarding/", {"agreement_accepted": True}, format="json")

        after = self.client.get("/api/v1/users/me/")
        self.assertTrue(after.data["has_completed_onboarding"])

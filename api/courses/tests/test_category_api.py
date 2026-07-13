from decimal import Decimal

from rest_framework import status
from rest_framework.test import APITestCase

from api.courses.enums import TrackPreference
from api.courses.models import Category
from api.courses.tests.factories import make_category, make_user
from api.users.enums import UserRole


class CategoryApiTests(APITestCase):
    def setUp(self):
        self.admin = make_user(role=UserRole.ADMIN)
        self.creator = make_user(role=UserRole.COURSE_CREATOR)

    def test_list_requires_authentication(self):
        response = self.client.get("/api/v1/categories/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_authenticated_user_can_list_categories(self):
        make_category(name="Web Dev", track_preference=TrackPreference.OPEN)
        self.client.force_authenticate(self.creator)

        response = self.client.get("/api/v1/categories/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["data"]["results"]), 1)

    def test_filter_by_track_preference(self):
        make_category(name="Web Dev", track_preference=TrackPreference.OPEN)
        make_category(name="AI 101", track_preference=TrackPreference.AI_PREFERRED)
        self.client.force_authenticate(self.creator)

        response = self.client.get("/api/v1/categories/", {"track_preference": "AI_PREFERRED"})
        results = response.data["data"]["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "AI 101")

    def test_creator_cannot_create_category(self):
        self.client.force_authenticate(self.creator)

        response = self.client.post(
            "/api/v1/categories/",
            {"name": "New Cat", "creator_price": "100.00", "track_preference": "OPEN", "status": "ACTIVE"},
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_create_category(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            "/api/v1/categories/",
            {"name": "New Cat", "creator_price": "100.00", "track_preference": "OPEN", "status": "ACTIVE"},
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(Category.objects.filter(name="New Cat").exists())

    def test_admin_can_update_price(self):
        category = make_category(creator_price=Decimal("50.00"))
        self.client.force_authenticate(self.admin)

        response = self.client.patch(
            f"/api/v1/categories/{category.id}/", {"creator_price": "75.00"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        category.refresh_from_db()
        self.assertEqual(category.creator_price, Decimal("75.00"))

    def test_admin_can_delete_category(self):
        category = make_category()
        self.client.force_authenticate(self.admin)

        response = self.client.delete(f"/api/v1/categories/{category.id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Category.objects.filter(id=category.id).exists())

    def test_duplicate_name_rejected(self):
        make_category(name="Dup")
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            "/api/v1/categories/",
            {"name": "Dup", "creator_price": "100.00", "track_preference": "OPEN", "status": "ACTIVE"},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

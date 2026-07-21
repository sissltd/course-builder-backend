from rest_framework import status
from rest_framework.test import APITestCase

from api.courses.models import Topic
from api.courses.tests.factories import make_category, make_topic, make_user
from api.users.enums import UserRole


class TopicApiTests(APITestCase):
    def setUp(self):
        self.admin = make_user(role=UserRole.ADMIN)
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.reviewer = make_user(role=UserRole.CREATOR_REVIEWER)
        self.category = make_category(name="Software Engineering")

    def test_list_requires_authentication(self):
        response = self.client.get("/api/v1/topics/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_authenticated_user_can_list_topics(self):
        make_topic(category=self.category, name="Frontend Development")
        self.client.force_authenticate(self.creator)

        response = self.client.get("/api/v1/topics/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["data"]["results"]), 1)

    def test_filter_by_category(self):
        other_category = make_category(name="Design")
        make_topic(category=self.category, name="Frontend Development")
        make_topic(category=other_category, name="UX Research")
        self.client.force_authenticate(self.creator)

        response = self.client.get(
            "/api/v1/topics/", {"category": str(self.category.id)}
        )
        results = response.data["data"]["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "Frontend Development")

    def test_creator_cannot_create_topic(self):
        self.client.force_authenticate(self.creator)

        response = self.client.post(
            "/api/v1/topics/",
            {
                "category": str(self.category.id),
                "name": "Rust Development",
                "creator_price": "25.00",
                "status": "ACTIVE",
            },
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_create_topic(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            "/api/v1/topics/",
            {
                "category": str(self.category.id),
                "name": "Rust Development",
                "creator_price": "25.00",
                "status": "ACTIVE",
            },
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(Topic.objects.filter(name="Rust Development").exists())

    def test_reviewer_can_create_topic(self):
        self.client.force_authenticate(self.reviewer)

        response = self.client.post(
            "/api/v1/topics/",
            {
                "category": str(self.category.id),
                "name": "Rust Development",
                "creator_price": "25.00",
                "status": "ACTIVE",
            },
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_reviewer_can_update_price(self):
        topic = make_topic(category=self.category, name="Frontend Development")
        self.client.force_authenticate(self.reviewer)

        response = self.client.patch(
            f"/api/v1/topics/{topic.id}/", {"creator_price": "30.00"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_duplicate_name_within_category_rejected(self):
        make_topic(category=self.category, name="Frontend Development")
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            "/api/v1/topics/",
            {
                "category": str(self.category.id),
                "name": "Frontend Development",
                "creator_price": "25.00",
                "status": "ACTIVE",
            },
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_same_name_in_different_category_allowed(self):
        other_category = make_category(name="Design")
        make_topic(category=self.category, name="Frontend Development")
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            "/api/v1/topics/",
            {
                "category": str(other_category.id),
                "name": "Frontend Development",
                "creator_price": "25.00",
                "status": "ACTIVE",
            },
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_admin_can_delete_topic(self):
        topic = make_topic(category=self.category)
        self.client.force_authenticate(self.admin)

        response = self.client.delete(f"/api/v1/topics/{topic.id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Topic.objects.filter(id=topic.id).exists())

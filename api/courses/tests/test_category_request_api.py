from django.core import mail
from rest_framework import status
from rest_framework.test import APITestCase

from api.courses.enums import CategoryRequestStatus
from api.categories.models import Category
from api.courses.models import CategoryRequest
from api.courses.tests.factories import make_category_request, make_user
from api.users.enums import UserRole


class CategoryRequestApiTests(APITestCase):
    def setUp(self):
        self.admin = make_user(role=UserRole.ADMIN)
        self.reviewer = make_user(role=UserRole.CREATOR_REVIEWER)
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.other_creator = make_user(role=UserRole.COURSE_CREATOR)

    def test_create_requires_authentication(self):
        response = self.client.post(
            "/api/v1/category-requests/", {"name": "Robotics"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_creator_can_submit_request(self):
        self.client.force_authenticate(self.creator)

        response = self.client.post(
            "/api/v1/category-requests/", {"name": "Robotics"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], CategoryRequestStatus.PENDING)
        self.assertTrue(
            CategoryRequest.objects.filter(
                requested_by=self.creator, name="Robotics"
            ).exists()
        )

    def test_creator_lists_only_own_requests(self):
        make_category_request(requested_by=self.creator)
        make_category_request(requested_by=self.other_creator)
        self.client.force_authenticate(self.creator)

        response = self.client.get("/api/v1/category-requests/")
        self.assertEqual(len(response.data["data"]["results"]), 1)

    def test_admin_lists_all_requests(self):
        make_category_request(requested_by=self.creator)
        make_category_request(requested_by=self.other_creator)
        self.client.force_authenticate(self.admin)

        response = self.client.get("/api/v1/category-requests/")
        self.assertEqual(len(response.data["data"]["results"]), 2)

    def test_reviewer_lists_all_requests(self):
        make_category_request(requested_by=self.creator)
        make_category_request(requested_by=self.other_creator)
        self.client.force_authenticate(self.reviewer)

        response = self.client.get("/api/v1/category-requests/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["data"]["results"]), 2)

    def test_reviewer_can_approve(self):
        category_request = make_category_request(
            requested_by=self.creator, name="Robotics"
        )
        self.client.force_authenticate(self.reviewer)

        response = self.client.post(
            f"/api/v1/category-requests/{category_request.id}/approve/",
            {"creator_price": "25.00"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(Category.objects.filter(name="Robotics").exists())

    def test_reviewer_can_reject(self):
        category_request = make_category_request(requested_by=self.creator)
        self.client.force_authenticate(self.reviewer)

        response = self.client.post(
            f"/api/v1/category-requests/{category_request.id}/reject/"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], CategoryRequestStatus.REJECTED)

    def test_creator_cannot_approve(self):
        category_request = make_category_request(requested_by=self.creator)
        self.client.force_authenticate(self.creator)

        response = self.client.post(
            f"/api/v1/category-requests/{category_request.id}/approve/",
            {"creator_price": "25.00"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_approve_creates_category_and_emails_requester(self):
        category_request = make_category_request(
            requested_by=self.creator, name="Robotics"
        )
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            f"/api/v1/category-requests/{category_request.id}/approve/",
            {"creator_price": "25.00", "track_preference": "OPEN"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], CategoryRequestStatus.APPROVED)
        self.assertTrue(Category.objects.filter(name="Robotics").exists())
        category_request.refresh_from_db()
        self.assertIsNotNone(category_request.resulting_category)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.creator.email])

    def test_admin_reject_does_not_send_email(self):
        category_request = make_category_request(requested_by=self.creator)
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            f"/api/v1/category-requests/{category_request.id}/reject/"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], CategoryRequestStatus.REJECTED)
        self.assertEqual(len(mail.outbox), 0)

    def test_approve_duplicate_name_rejected(self):
        Category.objects.create(name="Robotics", creator_price="10.00")
        category_request = make_category_request(
            requested_by=self.creator, name="Robotics"
        )
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            f"/api/v1/category-requests/{category_request.id}/approve/",
            {"creator_price": "25.00"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

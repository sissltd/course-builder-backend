from datetime import timedelta

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from api.catalog.enums import ReservationStatus
from api.courses.tests.factories import (
    make_category,
    make_topic,
    make_topic_reservation_request,
    make_user,
)
from api.users.enums import UserRole


class AdminReservationApiTests(APITestCase):
    def setUp(self):
        self.admin = make_user(role=UserRole.ADMIN)
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.category = make_category()
        self.reservation_request = make_topic_reservation_request(
            requested_by=self.creator,
            category=self.category,
            name="AWS Cloud Practitioner",
        )

    def test_request_list_contains_requester_and_supports_filters(self):
        self.client.force_authenticate(self.admin)

        response = self.client.get(
            "/api/v1/admin/reservations/requests/",
            {
                "status": ReservationStatus.PENDING,
                "requested_by": self.creator.id,
                "search": "AWS",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        result = response.data["data"]["results"][0]
        self.assertEqual(result["requested_by"]["id"], str(self.creator.id))
        self.assertIsNone(result["reviewed_by"])

    def test_admin_can_reject_request(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            f"/api/v1/admin/reservations/requests/{self.reservation_request.id}/reject/",
            {"reason": "A matching topic already exists."},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], ReservationStatus.REJECTED)
        self.assertEqual(response.data["reviewed_by"]["id"], str(self.admin.id))

    def test_active_list_excludes_expired_reservations_and_can_release(self):
        active = make_topic(
            category=self.category,
            name="Project Management",
            reserved_by=self.creator,
            reserved_until=timezone.localdate() + timedelta(days=10),
        )
        make_topic(
            category=self.category,
            reserved_by=self.creator,
            reserved_until=timezone.localdate() - timedelta(days=1),
        )
        self.client.force_authenticate(self.admin)

        response = self.client.get(
            "/api/v1/admin/reservations/active/", {"search": "Project"}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["data"]["results"]
        self.assertEqual([item["id"] for item in results], [str(active.id)])

        release = self.client.post(
            f"/api/v1/admin/reservations/active/{active.id}/release/"
        )
        self.assertEqual(release.status_code, status.HTTP_200_OK)
        self.assertIsNone(release.data["reserved_by"])

    def test_creator_cannot_access_admin_reservations(self):
        self.client.force_authenticate(self.creator)

        response = self.client.get("/api/v1/admin/reservations/requests/")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

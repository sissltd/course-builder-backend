from datetime import timedelta

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from api.catalog.enums import ReservationStatus
from api.catalog.models import Topic, TopicReservationRequest
from api.courses.tests.factories import (
    make_category,
    make_topic,
    make_topic_reservation_request,
    make_user,
)
from api.users.enums import UserRole


class TopicReservationRequestApiTests(APITestCase):
    def setUp(self):
        self.admin = make_user(role=UserRole.ADMIN)
        self.reviewer = make_user(role=UserRole.CREATOR_REVIEWER)
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.other_creator = make_user(role=UserRole.COURSE_CREATOR)
        self.category = make_category()

    def test_create_requires_authentication(self):
        response = self.client.post(
            "/api/v1/topic-reservations/",
            {"name": "New Topic", "category": str(self.category.id)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_creator_can_submit_request(self):
        self.client.force_authenticate(self.creator)

        response = self.client.post(
            "/api/v1/topic-reservations/",
            {"name": "New Topic", "category": str(self.category.id)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], ReservationStatus.PENDING)
        self.assertIsNone(response.data["topic"])
        self.assertTrue(
            TopicReservationRequest.objects.filter(
                requested_by=self.creator, name="New Topic", category=self.category
            ).exists()
        )

    def test_duplicate_name_still_accepted_at_submit_time(self):
        # No automatic duplicate check - it's the reviewer's call.
        make_topic(category=self.category, name="Existing Topic")
        self.client.force_authenticate(self.creator)

        response = self.client.post(
            "/api/v1/topic-reservations/",
            {"name": "Existing Topic", "category": str(self.category.id)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_creator_lists_only_own_requests(self):
        make_topic_reservation_request(
            requested_by=self.creator, category=self.category
        )
        make_topic_reservation_request(
            requested_by=self.other_creator, category=self.category
        )
        self.client.force_authenticate(self.creator)

        response = self.client.get("/api/v1/topic-reservations/")
        self.assertEqual(len(response.data["data"]["results"]), 1)

    def test_admin_lists_all_requests(self):
        make_topic_reservation_request(
            requested_by=self.creator, category=self.category
        )
        make_topic_reservation_request(
            requested_by=self.other_creator, category=self.category
        )
        self.client.force_authenticate(self.admin)

        response = self.client.get("/api/v1/topic-reservations/")
        self.assertEqual(len(response.data["data"]["results"]), 2)

    def test_creator_cannot_approve(self):
        request = make_topic_reservation_request(
            requested_by=self.creator, category=self.category
        )
        self.client.force_authenticate(self.creator)

        response = self.client.post(f"/api/v1/topic-reservations/{request.id}/approve/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_approve_creates_and_reserves_topic(self):
        request = make_topic_reservation_request(
            requested_by=self.creator, category=self.category, name="New Topic"
        )
        self.client.force_authenticate(self.admin)

        response = self.client.post(f"/api/v1/topic-reservations/{request.id}/approve/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], ReservationStatus.APPROVED)
        self.assertIsNotNone(response.data["topic"])

        topic = Topic.objects.get(name="New Topic", category=self.category)
        self.assertEqual(topic.reserved_by_id, self.creator.id)
        self.assertTrue(topic.is_currently_reserved)
        self.assertEqual(
            topic.reserved_until, timezone.localdate() + timedelta(days=30)
        )

    def test_admin_approve_rejects_duplicate_name_in_category(self):
        make_topic(category=self.category, name="Existing Topic")
        request = make_topic_reservation_request(
            requested_by=self.creator, category=self.category, name="Existing Topic"
        )
        self.client.force_authenticate(self.admin)

        response = self.client.post(f"/api/v1/topic-reservations/{request.id}/approve/")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reviewer_can_reject_with_reason(self):
        request = make_topic_reservation_request(
            requested_by=self.creator, category=self.category
        )
        self.client.force_authenticate(self.reviewer)

        response = self.client.post(
            f"/api/v1/topic-reservations/{request.id}/reject/",
            {"reason": "This topic already exists in our database."},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], ReservationStatus.REJECTED)
        self.assertEqual(
            response.data["rejection_reason"],
            "This topic already exists in our database.",
        )
        self.assertIsNone(response.data["topic"])


class ExistingTopicReservationApiTests(APITestCase):
    """Reserving an existing topic happens via course creation, not this
    viewset - see course_service.create_draft_course."""

    def setUp(self):
        self.admin = make_user(role=UserRole.ADMIN)
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.topic = make_topic()

    def test_release_reservation(self):
        self.topic.reserved_by = self.creator
        self.topic.reserved_until = timezone.localdate() + timedelta(days=10)
        self.topic.save()
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            f"/api/v1/topics/{self.topic.id}/release-reservation/"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["is_currently_reserved"])
        self.topic.refresh_from_db()
        self.assertIsNone(self.topic.reserved_until)

    def test_creator_cannot_release_reservation(self):
        self.topic.reserved_by = self.creator
        self.topic.reserved_until = timezone.localdate() + timedelta(days=10)
        self.topic.save()
        self.client.force_authenticate(self.creator)

        response = self.client.post(
            f"/api/v1/topics/{self.topic.id}/release-reservation/"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

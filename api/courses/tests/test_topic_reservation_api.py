from datetime import timedelta

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from api.courses.enums import ReservationStatus
from api.courses.models import TopicReservationRequest
from api.courses.tests.factories import (
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
        self.topic = make_topic()

    def test_create_requires_authentication(self):
        response = self.client.post(
            "/api/v1/topic-reservations/",
            {"topic": str(self.topic.id)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_creator_can_submit_request(self):
        self.client.force_authenticate(self.creator)

        response = self.client.post(
            "/api/v1/topic-reservations/",
            {"topic": str(self.topic.id)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], ReservationStatus.PENDING)
        self.assertTrue(
            TopicReservationRequest.objects.filter(
                requested_by=self.creator, topic=self.topic
            ).exists()
        )

    def test_duplicate_pending_request_rejected(self):
        make_topic_reservation_request(topic=self.topic, requested_by=self.creator)
        self.client.force_authenticate(self.other_creator)

        response = self.client.post(
            "/api/v1/topic-reservations/",
            {"topic": str(self.topic.id)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_already_reserved_topic_rejected(self):
        self.topic.reserved_by = self.other_creator
        self.topic.reserved_until = timezone.localdate() + timedelta(days=10)
        self.topic.save()
        self.client.force_authenticate(self.creator)

        response = self.client.post(
            "/api/v1/topic-reservations/",
            {"topic": str(self.topic.id)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_creator_lists_only_own_requests(self):
        make_topic_reservation_request(topic=self.topic, requested_by=self.creator)
        make_topic_reservation_request(
            topic=make_topic(), requested_by=self.other_creator
        )
        self.client.force_authenticate(self.creator)

        response = self.client.get("/api/v1/topic-reservations/")
        self.assertEqual(len(response.data["data"]["results"]), 1)

    def test_admin_lists_all_requests(self):
        make_topic_reservation_request(topic=self.topic, requested_by=self.creator)
        make_topic_reservation_request(
            topic=make_topic(), requested_by=self.other_creator
        )
        self.client.force_authenticate(self.admin)

        response = self.client.get("/api/v1/topic-reservations/")
        self.assertEqual(len(response.data["data"]["results"]), 2)

    def test_creator_cannot_approve(self):
        request = make_topic_reservation_request(
            topic=self.topic, requested_by=self.creator
        )
        self.client.force_authenticate(self.creator)

        response = self.client.post(
            f"/api/v1/topic-reservations/{request.id}/approve/"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_approve_reserves_topic(self):
        request = make_topic_reservation_request(
            topic=self.topic, requested_by=self.creator
        )
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            f"/api/v1/topic-reservations/{request.id}/approve/"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], ReservationStatus.APPROVED)

        self.topic.refresh_from_db()
        self.assertEqual(self.topic.reserved_by_id, self.creator.id)
        self.assertTrue(self.topic.is_currently_reserved)
        self.assertEqual(
            self.topic.reserved_until, timezone.localdate() + timedelta(days=30)
        )

    def test_reviewer_can_reject(self):
        request = make_topic_reservation_request(
            topic=self.topic, requested_by=self.creator
        )
        self.client.force_authenticate(self.reviewer)

        response = self.client.post(
            f"/api/v1/topic-reservations/{request.id}/reject/"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], ReservationStatus.REJECTED)
        self.topic.refresh_from_db()
        self.assertFalse(self.topic.is_currently_reserved)

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

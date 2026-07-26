from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from api.courses.enums import ReservationStatus
from api.courses.services import topic_reservation_service
from api.courses.tests.factories import (
    make_topic,
    make_topic_reservation_request,
    make_user,
)
from api.notification.models import Notification
from api.platform.services import platform_settings_service
from api.users.enums import UserRole


class SubmitRequestTests(TestCase):
    def test_creates_pending_request_and_notifies_managers(self):
        creator = make_user()
        admin = make_user(role=UserRole.ADMIN)
        topic = make_topic()

        request = topic_reservation_service.submit_request(user=creator, topic=topic)

        self.assertEqual(request.status, ReservationStatus.PENDING)
        self.assertTrue(
            Notification.objects.filter(
                receiver=admin, title="New topic reservation request"
            ).exists()
        )

    def test_raises_when_topic_already_reserved(self):
        creator = make_user()
        topic = make_topic()
        topic.reserved_by = make_user()
        topic.reserved_until = timezone.localdate() + timedelta(days=5)
        topic.save()

        with self.assertRaises(ValidationError):
            topic_reservation_service.submit_request(user=creator, topic=topic)

    def test_raises_when_pending_request_already_exists(self):
        topic = make_topic()
        make_topic_reservation_request(topic=topic)

        with self.assertRaises(ValidationError):
            topic_reservation_service.submit_request(user=make_user(), topic=topic)


class ApproveRequestTests(TestCase):
    def test_reserves_topic_using_platform_settings_expiry(self):
        platform_settings_service.update_settings(topic_reservation_expiry_days=10)
        creator = make_user()
        topic = make_topic()
        request = make_topic_reservation_request(topic=topic, requested_by=creator)
        admin = make_user(role=UserRole.ADMIN)

        result = topic_reservation_service.approve_request(
            request=request, actor=admin
        )

        self.assertEqual(result.status, ReservationStatus.APPROVED)
        topic.refresh_from_db()
        self.assertEqual(topic.reserved_by_id, creator.id)
        self.assertEqual(
            topic.reserved_until, timezone.localdate() + timedelta(days=10)
        )

    def test_raises_when_not_pending(self):
        request = make_topic_reservation_request()
        request.status = ReservationStatus.APPROVED
        request.save()

        with self.assertRaises(ValidationError):
            topic_reservation_service.approve_request(
                request=request, actor=make_user(role=UserRole.ADMIN)
            )


class ReleaseReservationTests(TestCase):
    def test_clears_active_reservation(self):
        topic = make_topic(
            reserved_by=make_user(), reserved_until=timezone.localdate()
        )
        admin = make_user(role=UserRole.ADMIN)

        result = topic_reservation_service.release_reservation(
            topic=topic, actor=admin
        )

        self.assertIsNone(result.reserved_by_id)
        self.assertIsNone(result.reserved_until)

    def test_noop_when_not_reserved(self):
        topic = make_topic()
        admin = make_user(role=UserRole.ADMIN)

        result = topic_reservation_service.release_reservation(
            topic=topic, actor=admin
        )

        self.assertFalse(result.is_currently_reserved)

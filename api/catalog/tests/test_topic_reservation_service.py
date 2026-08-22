from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from api.catalog.enums import ReservationStatus
from api.catalog.models import Topic
from api.catalog.services import topic_reservation_service
from api.courses.tests.factories import (
    make_category,
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
        category = make_category()

        request = topic_reservation_service.submit_request(
            user=creator, name="Fundamentals of Programming", category=category
        )

        self.assertEqual(request.status, ReservationStatus.PENDING)
        self.assertEqual(request.name, "Fundamentals of Programming")
        self.assertIsNone(request.topic_id)
        self.assertTrue(
            Notification.objects.filter(
                receiver=admin, title="New topic request"
            ).exists()
        )

    def test_duplicate_name_still_creates_a_pending_request(self):
        # No automatic duplicate check at submit time - that's left to the
        # reviewer's judgment during approve/reject.
        category = make_category()
        make_topic(category=category, name="Existing Topic")

        request = topic_reservation_service.submit_request(
            user=make_user(), name="Existing Topic", category=category
        )

        self.assertEqual(request.status, ReservationStatus.PENDING)


class ApproveRequestTests(TestCase):
    def test_creates_topic_and_reserves_it_using_platform_settings_expiry(self):
        platform_settings_service.update_settings(topic_reservation_expiry_days=10)
        creator = make_user()
        category = make_category(creator_price=Decimal("120.00"))
        request = make_topic_reservation_request(
            requested_by=creator, category=category, name="New Topic"
        )
        admin = make_user(role=UserRole.ADMIN)

        result = topic_reservation_service.approve_request(request=request, actor=admin)

        self.assertEqual(result.status, ReservationStatus.APPROVED)
        self.assertIsNotNone(result.topic_id)
        topic = Topic.objects.get(id=result.topic_id)
        self.assertEqual(topic.name, "New Topic")
        self.assertEqual(topic.category_id, category.id)
        self.assertEqual(topic.creator_price, Decimal("120.00"))
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

    def test_raises_when_name_already_taken_in_category(self):
        category = make_category()
        make_topic(category=category, name="Existing Topic")
        request = make_topic_reservation_request(
            category=category, name="Existing Topic"
        )

        with self.assertRaises(ValidationError):
            topic_reservation_service.approve_request(
                request=request, actor=make_user(role=UserRole.ADMIN)
            )


class RejectRequestTests(TestCase):
    def test_rejects_with_reason_and_creates_no_topic(self):
        request = make_topic_reservation_request()
        admin = make_user(role=UserRole.ADMIN)

        result = topic_reservation_service.reject_request(
            request=request,
            actor=admin,
            reason="This topic already exists in our database.",
        )

        self.assertEqual(result.status, ReservationStatus.REJECTED)
        self.assertEqual(
            result.rejection_reason, "This topic already exists in our database."
        )
        self.assertIsNone(result.topic_id)

    def test_raises_when_not_pending(self):
        request = make_topic_reservation_request()
        request.status = ReservationStatus.REJECTED
        request.save()

        with self.assertRaises(ValidationError):
            topic_reservation_service.reject_request(
                request=request, actor=make_user(role=UserRole.ADMIN)
            )


class ReleaseReservationTests(TestCase):
    def test_clears_active_reservation(self):
        topic = make_topic(reserved_by=make_user(), reserved_until=timezone.localdate())
        admin = make_user(role=UserRole.ADMIN)

        result = topic_reservation_service.release_reservation(topic=topic, actor=admin)

        self.assertIsNone(result.reserved_by_id)
        self.assertIsNone(result.reserved_until)

    def test_noop_when_not_reserved(self):
        topic = make_topic()
        admin = make_user(role=UserRole.ADMIN)

        result = topic_reservation_service.release_reservation(topic=topic, actor=admin)

        self.assertFalse(result.is_currently_reserved)

from decimal import Decimal

from django.core import mail
from django.test import TestCase
from rest_framework.exceptions import ValidationError

from api.courses.enums import CategoryRequestStatus
from api.categories.models import Category
from api.courses.services import category_request_service
from api.courses.tests.factories import make_category_request, make_user
from api.notification.models import Notification
from api.users.enums import UserRole


class SubmitRequestTests(TestCase):
    def test_creates_pending_request_and_notifies_admins_and_reviewers(self):
        creator = make_user()
        admin = make_user(role=UserRole.ADMIN)
        reviewer = make_user(role=UserRole.CREATOR_REVIEWER)

        category_request = category_request_service.submit_request(
            user=creator, name="Robotics"
        )

        self.assertEqual(category_request.status, CategoryRequestStatus.PENDING)
        self.assertTrue(
            Notification.objects.filter(
                receiver=admin, title="New category request"
            ).exists()
        )
        self.assertTrue(
            Notification.objects.filter(
                receiver=reviewer, title="New category request"
            ).exists()
        )
        self.assertEqual(len(mail.outbox), 0)


class ApproveRequestTests(TestCase):
    def test_creates_category_sets_resulting_category_and_emails_requester(self):
        creator = make_user()
        admin = make_user(role=UserRole.ADMIN)
        category_request = make_category_request(requested_by=creator, name="Robotics")

        result = category_request_service.approve_request(
            category_request=category_request,
            actor=admin,
            creator_price=Decimal("30.00"),
        )

        self.assertEqual(result.status, CategoryRequestStatus.APPROVED)
        self.assertIsNotNone(result.resulting_category)
        self.assertEqual(result.resulting_category.name, "Robotics")
        self.assertEqual(result.resulting_category.creator_price, Decimal("30.00"))
        self.assertEqual(result.reviewed_by, admin)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [creator.email])

    def test_reviewer_can_approve(self):
        creator = make_user()
        reviewer = make_user(role=UserRole.CREATOR_REVIEWER)
        category_request = make_category_request(requested_by=creator, name="Robotics")

        result = category_request_service.approve_request(
            category_request=category_request,
            actor=reviewer,
            creator_price=Decimal("30.00"),
        )

        self.assertEqual(result.status, CategoryRequestStatus.APPROVED)
        self.assertEqual(result.reviewed_by, reviewer)

    def test_raises_when_not_pending(self):
        category_request = make_category_request()
        category_request.status = CategoryRequestStatus.APPROVED
        category_request.save()
        admin = make_user(role=UserRole.ADMIN)

        with self.assertRaises(ValidationError):
            category_request_service.approve_request(
                category_request=category_request,
                actor=admin,
                creator_price=Decimal("30.00"),
            )

    def test_raises_on_duplicate_category_name(self):
        Category.objects.create(name="Robotics", creator_price=Decimal("10.00"))
        category_request = make_category_request(name="Robotics")
        admin = make_user(role=UserRole.ADMIN)

        with self.assertRaises(ValidationError):
            category_request_service.approve_request(
                category_request=category_request,
                actor=admin,
                creator_price=Decimal("30.00"),
            )


class RejectRequestTests(TestCase):
    def test_rejects_pending_request(self):
        category_request = make_category_request()
        admin = make_user(role=UserRole.ADMIN)

        result = category_request_service.reject_request(
            category_request=category_request, actor=admin
        )

        self.assertEqual(result.status, CategoryRequestStatus.REJECTED)
        self.assertEqual(result.reviewed_by, admin)
        self.assertEqual(len(mail.outbox), 0)

    def test_raises_when_not_pending(self):
        category_request = make_category_request()
        category_request.status = CategoryRequestStatus.REJECTED
        category_request.save()
        admin = make_user(role=UserRole.ADMIN)

        with self.assertRaises(ValidationError):
            category_request_service.reject_request(
                category_request=category_request, actor=admin
            )

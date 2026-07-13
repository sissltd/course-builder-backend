from decimal import Decimal

from rest_framework import status
from rest_framework.test import APITestCase

from api.courses.enums import CourseStatus
from api.courses.models import ReviewAction
from api.courses.services import course_service
from api.courses.tests.factories import build_compliant_course, make_category, make_user
from api.users.enums import UserRole
from api.wallet.services import wallet_service


class ReviewQueueApiTests(APITestCase):
    def setUp(self):
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.reviewer = make_user(role=UserRole.CREATOR_REVIEWER)
        self.admin = make_user(role=UserRole.ADMIN)
        self.category = make_category(creator_price=Decimal("120.00"))

    def _submitted_course(self):
        course = build_compliant_course(creator=self.creator, category=self.category)
        return course_service.submit_course(course=course, actor=self.creator)

    def test_queue_lists_submitted_and_in_review_ordered_by_submitted_at(self):
        course1 = self._submitted_course()
        course2 = self._submitted_course()
        self.client.force_authenticate(self.reviewer)

        response = self.client.get("/api/v1/review-queue/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [r["id"] for r in response.data["data"]["results"]]
        self.assertEqual(ids, [str(course1.id), str(course2.id)])

    def test_queue_filter_by_status(self):
        self._submitted_course()
        self.client.force_authenticate(self.reviewer)

        response = self.client.get("/api/v1/review-queue/", {"status": "SUBMITTED"})
        self.assertEqual(len(response.data["data"]["results"]), 1)

    def test_claim_transitions_to_in_review(self):
        course = self._submitted_course()
        self.client.force_authenticate(self.reviewer)

        response = self.client.post(f"/api/v1/review-queue/{course.id}/claim/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        course.refresh_from_db()
        self.assertEqual(course.status, CourseStatus.IN_REVIEW)

    def test_approve_happy_path_credits_wallet_and_notifies(self):
        course = self._submitted_course()
        self.client.force_authenticate(self.reviewer)

        response = self.client.post(f"/api/v1/review-queue/{course.id}/approve/", {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        course.refresh_from_db()
        self.assertEqual(course.status, CourseStatus.APPROVED)
        self.assertTrue(ReviewAction.objects.filter(course=course, action="APPROVE").exists())

        wallet = wallet_service.get_or_create_wallet(user=self.creator)
        self.assertEqual(wallet.balance, Decimal("120.00"))

    def test_double_approve_returns_400(self):
        course = self._submitted_course()
        self.client.force_authenticate(self.reviewer)

        self.client.post(f"/api/v1/review-queue/{course.id}/approve/", {}, format="json")
        response = self.client.post(f"/api/v1/review-queue/{course.id}/approve/", {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reject_requires_summary(self):
        course = self._submitted_course()
        self.client.force_authenticate(self.reviewer)

        response = self.client.post(
            f"/api/v1/review-queue/{course.id}/reject/", {"feedback": {}}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reject_reverts_course_to_draft(self):
        course = self._submitted_course()
        self.client.force_authenticate(self.reviewer)

        response = self.client.post(
            f"/api/v1/review-queue/{course.id}/reject/",
            {"feedback": {"summary": "Needs more detail"}},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        course.refresh_from_db()
        self.assertEqual(course.status, CourseStatus.DRAFT)
        self.assertIsNotNone(course.rejected_at)
        self.assertTrue(ReviewAction.objects.filter(course=course, action="REJECT").exists())

    def test_creator_cannot_review_own_course(self):
        course = self._submitted_course()
        self.client.force_authenticate(self.creator)

        response = self.client.post(f"/api/v1/review-queue/{course.id}/approve/", {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_non_reviewer_non_admin_forbidden(self):
        self._submitted_course()
        other = make_user(role=UserRole.COURSE_CREATOR)
        self.client.force_authenticate(other)

        response = self.client.get("/api/v1/review-queue/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_approve_without_reviewer_role(self):
        course = self._submitted_course()
        self.client.force_authenticate(self.admin)

        response = self.client.post(f"/api/v1/review-queue/{course.id}/approve/", {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

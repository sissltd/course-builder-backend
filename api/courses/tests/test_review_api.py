from decimal import Decimal

from rest_framework import status
from rest_framework.test import APITestCase

from api.courses.enums import CourseStatus
from api.courses.models import ReviewAction
from api.courses.services import course_service, review_service
from api.courses.tests.factories import build_compliant_course, make_category, make_user
from api.users.enums import UserRole
from api.users.models import UserActivityLog
from api.users.services import reviewer_availability_service
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

    def test_queue_includes_approved_and_published_by_default_and_via_filter(self):
        submitted_course = self._submitted_course()

        approved_course = self._submitted_course()
        review_service.approve_course(course=approved_course, reviewer=self.reviewer)

        published_course = self._submitted_course()
        review_service.approve_course(course=published_course, reviewer=self.reviewer)
        course_service.publish_course(course=published_course, actor=self.admin)

        self.client.force_authenticate(self.reviewer)

        response = self.client.get("/api/v1/review-queue/")
        ids = {r["id"] for r in response.data["data"]["results"]}
        self.assertEqual(
            ids,
            {str(submitted_course.id), str(approved_course.id), str(published_course.id)},
        )

        response = self.client.get("/api/v1/review-queue/", {"status": "APPROVED"})
        ids = {r["id"] for r in response.data["data"]["results"]}
        self.assertEqual(ids, {str(approved_course.id)})

        response = self.client.get("/api/v1/review-queue/", {"status": "PUBLISHED"})
        ids = {r["id"] for r in response.data["data"]["results"]}
        self.assertEqual(ids, {str(published_course.id)})

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

        response = self.client.post(
            f"/api/v1/review-queue/{course.id}/approve/", {}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        course.refresh_from_db()
        self.assertEqual(course.status, CourseStatus.APPROVED)
        self.assertTrue(
            ReviewAction.objects.filter(course=course, action="APPROVE").exists()
        )

        wallet = wallet_service.get_or_create_wallet(user=self.creator)
        self.assertEqual(wallet.balance, Decimal("120.00"))

    def test_double_approve_returns_400(self):
        course = self._submitted_course()
        self.client.force_authenticate(self.reviewer)

        self.client.post(
            f"/api/v1/review-queue/{course.id}/approve/", {}, format="json"
        )
        response = self.client.post(
            f"/api/v1/review-queue/{course.id}/approve/", {}, format="json"
        )
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
        self.assertTrue(
            ReviewAction.objects.filter(course=course, action="REJECT").exists()
        )

    def test_creator_cannot_review_own_course(self):
        course = self._submitted_course()
        self.client.force_authenticate(self.creator)

        response = self.client.post(
            f"/api/v1/review-queue/{course.id}/approve/", {}, format="json"
        )
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

        response = self.client.post(
            f"/api/v1/review-queue/{course.id}/approve/", {}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_unavailable_reviewer_cannot_claim(self):
        course = self._submitted_course()
        reviewer_availability_service.update_availability(
            user=self.reviewer, is_available=False
        )
        self.client.force_authenticate(self.reviewer)

        response = self.client.post(f"/api/v1/review-queue/{course.id}/claim/")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unavailable_reviewer_cannot_approve(self):
        course = self._submitted_course()
        reviewer_availability_service.update_availability(
            user=self.reviewer, is_available=False
        )
        self.client.force_authenticate(self.reviewer)

        response = self.client.post(
            f"/api/v1/review-queue/{course.id}/approve/", {}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unavailable_reviewer_cannot_reject(self):
        course = self._submitted_course()
        reviewer_availability_service.update_availability(
            user=self.reviewer, is_available=False
        )
        self.client.force_authenticate(self.reviewer)

        response = self.client.post(
            f"/api/v1/review-queue/{course.id}/reject/",
            {"feedback": {"summary": "x"}},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_idempotent_claim_still_works_after_going_unavailable(self):
        course = self._submitted_course()
        self.client.force_authenticate(self.reviewer)
        self.client.post(f"/api/v1/review-queue/{course.id}/claim/")

        reviewer_availability_service.update_availability(
            user=self.reviewer, is_available=False
        )
        response = self.client.post(f"/api/v1/review-queue/{course.id}/claim/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_claim_approve_reject_log_activity(self):
        course = self._submitted_course()
        self.client.force_authenticate(self.reviewer)

        self.client.post(f"/api/v1/review-queue/{course.id}/claim/")
        self.assertTrue(
            UserActivityLog.objects.filter(
                user=self.reviewer, action="COURSE_ASSIGNED", category="COURSE"
            ).exists()
        )

        self.client.post(
            f"/api/v1/review-queue/{course.id}/approve/", {}, format="json"
        )
        self.assertTrue(
            UserActivityLog.objects.filter(
                user=self.reviewer, action="COURSE_APPROVED", category="APPROVAL"
            ).exists()
        )

        course2 = self._submitted_course()
        self.client.post(
            f"/api/v1/review-queue/{course2.id}/reject/",
            {"feedback": {"summary": "Needs work"}},
            format="json",
        )
        self.assertTrue(
            UserActivityLog.objects.filter(
                user=self.reviewer, action="COURSE_REJECTED", category="APPROVAL"
            ).exists()
        )

    def test_submit_logs_activity_for_creator(self):
        self._submitted_course()

        self.assertTrue(
            UserActivityLog.objects.filter(
                user=self.creator, action="COURSE_SUBMITTED", category="SUBMISSION"
            ).exists()
        )

    def test_publish_logs_activity_for_actor(self):
        course = self._submitted_course()
        review_service.approve_course(course=course, reviewer=self.reviewer)
        self.client.force_authenticate(self.admin)

        self.client.post(f"/api/v1/courses/{course.id}/publish/")

        self.assertTrue(
            UserActivityLog.objects.filter(
                user=self.admin, action="COURSE_PUBLISHED", category="PUBLISH"
            ).exists()
        )

"""The three content seats a submission passes through, in order.

First Review and Second Review take a Creator Reviewer, Verification takes a
Verifier, and each seat must be a different person. Everything here goes
through the URLs the frontend calls, because the seat rules live in the
service but the claimant and role refusals are what a reviewer actually sees.
"""

from decimal import Decimal

from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework import exceptions, status
from rest_framework.test import APITestCase

from api.courses.enums import CourseStatus
from api.courses.models import Course, CourseVersion
from api.courses.services import course_appeal_service, course_service
from api.courses.tests.factories import (
    build_compliant_course,
    make_category,
    make_course_appeal,
    make_user,
)
from api.notification.models import Notification
from api.reviews.enums import ReviewActionType, ReviewStage
from api.reviews.models import ReviewAction, ReviewAssignment
from api.reviews.services import review_service
from api.users.enums import UserRole
from api.users.models import UserActivityLog

QUEUE_URL = "/api/v1/review-queue/"


class ReviewChainTests(APITestCase):
    def setUp(self):
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.first = make_user(role=UserRole.CREATOR_REVIEWER)
        self.second = make_user(role=UserRole.CREATOR_REVIEWER)
        self.verifier = make_user(role=UserRole.STAFF_VERIFIER)
        self.admin = make_user(role=UserRole.ADMIN)
        self.category = make_category(
            creator_price_beginner=Decimal("120.00"),
            creator_price_intermediate=Decimal("120.00"),
            creator_price_advanced=Decimal("120.00"),
        )
        CourseVersion.objects.get_or_create(label="1.0")

    # ── helpers ──────────────────────────────────────────────────────────

    def _submitted_course(self):
        course = build_compliant_course(creator=self.creator, category=self.category)
        return course_service.submit_course(course=course, actor=self.creator)

    def _claim(self, course, reviewer):
        self.client.force_authenticate(reviewer)
        return self.client.post(f"{QUEUE_URL}{course.id}/claim/", format="json")

    def _approve(self, course, reviewer):
        self.client.force_authenticate(reviewer)
        return self.client.post(f"{QUEUE_URL}{course.id}/approve/", format="json")

    def _reject(self, course, reviewer, summary="Needs more detail"):
        self.client.force_authenticate(reviewer)
        return self.client.post(
            f"{QUEUE_URL}{course.id}/reject/",
            {"feedback": {"summary": summary}},
            format="json",
        )

    def _take_seat(self, course, reviewer):
        """Claim then approve, asserting both succeeded."""

        self.assertEqual(self._claim(course, reviewer).status_code, status.HTTP_200_OK)
        response = self._approve(course, reviewer)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        course.refresh_from_db()
        return response

    # ── the chain ────────────────────────────────────────────────────────

    def test_three_seats_run_in_order_before_qa(self):
        course = self._submitted_course()
        self.assertEqual(course.review_stage, ReviewStage.CONTENT)

        self._take_seat(course, self.first)
        self.assertEqual(course.status, CourseStatus.SUBMITTED)
        self.assertEqual(course.review_stage, ReviewStage.SECOND_REVIEW)

        self._take_seat(course, self.second)
        self.assertEqual(course.status, CourseStatus.SUBMITTED)
        self.assertEqual(course.review_stage, ReviewStage.VERIFICATION)

        self._take_seat(course, self.verifier)
        self.assertEqual(course.status, CourseStatus.QA_VERIFICATION)
        self.assertEqual(course.review_stage, "")

        self.assertEqual(
            list(
                ReviewAction.objects.filter(
                    course=course, action=ReviewActionType.APPROVE
                )
                .order_by("created_datetime")
                .values_list("stage", flat=True)
            ),
            [
                ReviewStage.CONTENT,
                ReviewStage.SECOND_REVIEW,
                ReviewStage.VERIFICATION,
            ],
        )
        self.assertEqual(
            ReviewAssignment.objects.filter(
                course=course, completed_at__isnull=False
            ).count(),
            3,
        )

    def test_creator_is_notified_once_at_the_end_of_the_chain(self):
        course = self._submitted_course()

        def passed_content():
            return Notification.objects.filter(
                receiver=self.creator, title="Course passed content review"
            ).count()

        self._take_seat(course, self.first)
        self.assertEqual(passed_content(), 0)
        self._take_seat(course, self.second)
        self.assertEqual(passed_content(), 0)
        self._take_seat(course, self.verifier)
        self.assertEqual(passed_content(), 1)

    # ── who may sit where ────────────────────────────────────────────────

    def test_same_reviewer_cannot_take_the_second_seat(self):
        course = self._submitted_course()
        self._take_seat(course, self.first)

        claim = self._claim(course, self.first)

        self.assertEqual(claim.status_code, status.HTTP_403_FORBIDDEN)
        course.refresh_from_db()
        self.assertEqual(course.review_stage, ReviewStage.SECOND_REVIEW)
        self.assertEqual(course.status, CourseStatus.SUBMITTED)

    def test_verifier_cannot_take_a_content_review_seat(self):
        course = self._submitted_course()

        self.assertEqual(
            self._claim(course, self.verifier).status_code, status.HTTP_403_FORBIDDEN
        )

    def test_creator_reviewer_cannot_take_the_verification_seat(self):
        course = self._submitted_course()
        self._take_seat(course, self.first)
        self._take_seat(course, self.second)

        third = make_user(role=UserRole.CREATOR_REVIEWER)

        self.assertEqual(
            self._claim(course, third).status_code, status.HTTP_403_FORBIDDEN
        )

    # ── the claim is what entitles you to decide ─────────────────────────

    def test_reviewer_must_claim_before_deciding(self):
        course = self._submitted_course()

        response = self._approve(course, self.first)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        course.refresh_from_db()
        self.assertEqual(course.status, CourseStatus.SUBMITTED)
        self.assertEqual(course.review_stage, ReviewStage.CONTENT)

    def test_a_second_reviewer_cannot_decide_a_claimed_seat(self):
        course = self._submitted_course()
        self._claim(course, self.first)

        response = self._approve(course, self.second)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        course.refresh_from_db()
        self.assertEqual(course.status, CourseStatus.IN_REVIEW)

    def test_two_reviewers_cannot_hold_the_same_seat(self):
        course = self._submitted_course()
        self._claim(course, self.first)

        response = self._claim(course, self.second)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_decision_on_a_course_that_moved_on_is_refused(self):
        """Two reviewers deciding at once: the second is told to reload.

        Driven through the service because the view reloads the course on
        every request, so only a caller holding an instance from before the
        other decision can reach this guard - which is exactly what the
        losing side of a simultaneous decision holds.
        """

        course = self._submitted_course()
        stale = Course.objects.get(pk=course.pk)
        self._take_seat(course, self.first)

        with self.assertRaises(exceptions.ValidationError):
            review_service.approve_content(course=stale, reviewer=self.second)

        course.refresh_from_db()
        self.assertEqual(course.review_stage, ReviewStage.SECOND_REVIEW)

    # ── admin override ───────────────────────────────────────────────────

    def test_admin_may_decide_an_unclaimed_seat_and_it_is_logged(self):
        course = self._submitted_course()

        response = self._approve(course, self.admin)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        course.refresh_from_db()
        self.assertEqual(course.review_stage, ReviewStage.SECOND_REVIEW)
        self.assertTrue(
            UserActivityLog.objects.filter(
                user=self.admin, details__override=True
            ).exists()
        )

    def test_admin_may_take_over_a_claimed_seat_and_it_is_logged(self):
        course = self._submitted_course()
        self._claim(course, self.first)

        response = self._approve(course, self.admin)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        log = UserActivityLog.objects.filter(
            user=self.admin, details__override=True
        ).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.details["previous_reviewer_id"], str(self.first.id))
        self.assertEqual(
            ReviewAssignment.objects.get(
                course=course, stage=ReviewStage.CONTENT
            ).reviewer,
            self.admin,
        )

    # ── rejection ends the cycle ─────────────────────────────────────────

    def test_rejection_at_any_seat_returns_the_course_to_draft(self):
        course = self._submitted_course()
        self._take_seat(course, self.first)
        self.assertEqual(
            self._claim(course, self.second).status_code, status.HTTP_200_OK
        )

        response = self._reject(course, self.second)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        course.refresh_from_db()
        self.assertEqual(course.status, CourseStatus.DRAFT)
        self.assertEqual(course.review_stage, "")
        self.assertEqual(
            ReviewAction.objects.get(course=course, action=ReviewActionType.REJECT).stage,
            ReviewStage.SECOND_REVIEW,
        )

    def test_resubmission_restarts_the_chain_with_every_seat_cleared(self):
        course = self._submitted_course()
        self._take_seat(course, self.first)
        self._claim(course, self.second)
        self._reject(course, self.second)

        course.refresh_from_db()
        course_service.submit_course(course=course, actor=self.creator)

        course.refresh_from_db()
        self.assertEqual(course.status, CourseStatus.SUBMITTED)
        self.assertEqual(course.review_stage, ReviewStage.CONTENT)
        self.assertFalse(
            ReviewAssignment.objects.filter(course=course)
            .exclude(reviewer=None, claimed_at=None, completed_at=None)
            .exists()
        )
        # Seats are clear, so the reviewer locked out last cycle may take one.
        self.assertEqual(self._claim(course, self.first).status_code, status.HTTP_200_OK)

    def test_approved_appeal_restarts_the_chain(self):
        course = self._submitted_course()
        self._take_seat(course, self.first)
        self._claim(course, self.second)
        self._reject(course, self.second)
        appeal = make_course_appeal(course=course, submitted_by=self.creator)

        course_appeal_service.approve_appeal(appeal=appeal, actor=self.admin)

        course.refresh_from_db()
        self.assertEqual(course.status, CourseStatus.SUBMITTED)
        self.assertEqual(course.review_stage, ReviewStage.CONTENT)
        self.assertFalse(
            ReviewAssignment.objects.filter(
                course=course, completed_at__isnull=False
            ).exists()
        )

    # ── the pending queue only offers seats you can take ─────────────────

    def test_pending_queue_is_scoped_to_seats_the_reviewer_can_take(self):
        first_seat = self._submitted_course()
        verification_seat = self._submitted_course()
        self._take_seat(verification_seat, self.first)
        self._take_seat(verification_seat, self.second)

        self.client.force_authenticate(self.second)
        reviewer_ids = self._pending_ids()
        self.client.force_authenticate(self.verifier)
        verifier_ids = self._pending_ids()
        self.client.force_authenticate(self.admin)
        admin_ids = self._pending_ids()

        # self.second decided the Second Review seat on verification_seat, so
        # four eyes keeps it off their queue; the untouched course stays.
        self.assertEqual(reviewer_ids, {str(first_seat.id)})
        self.assertEqual(verifier_ids, {str(verification_seat.id)})
        self.assertEqual(admin_ids, {str(first_seat.id), str(verification_seat.id)})

    def _pending_ids(self):
        response = self.client.get(f"{QUEUE_URL}pending/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return {row["id"] for row in response.data["data"]["results"]}

    def test_pending_queue_cost_does_not_grow_with_the_number_of_courses(self):
        """The seat lockout is a subquery, so it must not cost a query a row."""

        self.client.force_authenticate(self.first)
        self._submitted_course()
        # One warm-up call first: the very first request of a session also
        # pays for rows this queue creates once (queue preferences), which
        # would otherwise show up as the 1-row reading being the expensive one.
        self._pending_ids()
        with CaptureQueriesContext(connection) as one_row:
            self._pending_ids()
        for _ in range(4):
            self._submitted_course()
        with CaptureQueriesContext(connection) as five_rows:
            self._pending_ids()

        self.assertEqual(len(one_row), len(five_rows))

    def test_review_stage_filter_selects_each_seat(self):
        """?review_stage= lives on the admin course list, not the queue."""

        first_seat = self._submitted_course()
        second_seat = self._submitted_course()
        self._take_seat(second_seat, self.first)
        self.client.force_authenticate(self.admin)

        for stage, expected in (
            (ReviewStage.CONTENT, {str(first_seat.id)}),
            (ReviewStage.SECOND_REVIEW, {str(second_seat.id)}),
            (ReviewStage.VERIFICATION, set()),
        ):
            with self.subTest(stage=stage):
                response = self.client.get(
                    f"/api/v1/admin/courses/?review_stage={stage}"
                )
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                ids = {row["id"] for row in response.data["data"]["results"]}
                self.assertEqual(ids, expected)

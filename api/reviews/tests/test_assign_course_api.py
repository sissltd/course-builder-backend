"""Assign Course: an admin puts a specific reviewer in a course's seat."""

from decimal import Decimal

from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework import status
from rest_framework.test import APITestCase

from api.courses.enums import CourseStatus
from api.courses.models import CourseVersion
from api.courses.services import course_service
from api.courses.tests.factories import build_compliant_course, make_category, make_user
from api.notification.models import Notification, NotificationPreference
from api.reviews.enums import ReviewStage
from api.reviews.models import ReviewAssignment
from api.users.enums import UserRole
from api.users.models import ReviewerAvailability, UserActivityLog

URL = "/api/v1/admin/courses/"


class _AssignBase(APITestCase):
    def setUp(self):
        self.admin = make_user(role=UserRole.ADMIN)
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.category = make_category(
            creator_price_beginner=Decimal("100"),
            creator_price_intermediate=Decimal("100"),
            creator_price_advanced=Decimal("100"),
        )
        CourseVersion.objects.get_or_create(label="1.0")
        self.course = course_service.submit_course(
            course=build_compliant_course(creator=self.creator, category=self.category),
            actor=self.creator,
        )
        self.client.force_authenticate(self.admin)

    def assign(self, reviewer, **extra):
        return self.client.post(
            f"{URL}{self.course.id}/assign/",
            {"reviewer_id": str(reviewer.id), **extra},
            format="json",
        )


class AssignCourseTests(_AssignBase):
    def test_assigning_puts_the_reviewer_in_the_open_seat(self):
        reviewer = make_user(role=UserRole.CREATOR_REVIEWER)

        response = self.assign(reviewer)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["seat"], ReviewStage.CONTENT)
        self.course.refresh_from_db()
        self.assertEqual(self.course.status, CourseStatus.IN_REVIEW)
        self.assertEqual(
            ReviewAssignment.objects.get(
                course=self.course, stage=ReviewStage.CONTENT
            ).reviewer,
            reviewer,
        )
        self.assertTrue(
            Notification.objects.filter(
                receiver=reviewer, title="Course assigned to you"
            ).exists()
        )
        self.assertTrue(
            UserActivityLog.objects.filter(
                user=reviewer, actor_user=self.admin, action="COURSE_ASSIGNED"
            ).exists()
        )

    def test_the_assigned_reviewer_can_then_decide_the_seat(self):
        reviewer = make_user(role=UserRole.CREATOR_REVIEWER)
        self.assign(reviewer)
        self.client.force_authenticate(reviewer)

        response = self.client.post(
            f"/api/v1/review-queue/{self.course.id}/approve/", {}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_the_reviewer_must_be_able_to_sit_the_seat(self):
        verifier = make_user(role=UserRole.STAFF_VERIFIER)

        response = self.assign(verifier)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_four_eyes_applies_to_the_assignee(self):
        first = make_user(role=UserRole.CREATOR_REVIEWER)
        self.assign(first)
        self.client.force_authenticate(first)
        self.client.post(
            f"/api/v1/review-queue/{self.course.id}/approve/", {}, format="json"
        )
        self.client.force_authenticate(self.admin)

        response = self.assign(first)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unavailable_reviewers_cannot_be_assigned(self):
        reviewer = make_user(role=UserRole.CREATOR_REVIEWER)
        ReviewerAvailability.objects.create(user=reviewer, is_available=False)

        response = self.assign(reviewer)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_held_seat_needs_replace(self):
        holder = make_user(role=UserRole.CREATOR_REVIEWER)
        newcomer = make_user(role=UserRole.CREATOR_REVIEWER)
        self.assign(holder)

        refused = self.assign(newcomer)
        replaced = self.assign(newcomer, replace=True)

        self.assertEqual(refused.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(replaced.status_code, status.HTTP_200_OK)
        self.assertTrue(
            Notification.objects.filter(
                receiver=holder, title="Course reassigned"
            ).exists()
        )

    def test_reassigning_the_holder_is_a_no_op(self):
        holder = make_user(role=UserRole.CREATOR_REVIEWER)
        self.assign(holder)

        response = self.assign(holder)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_notice_respects_the_new_course_assigned_preference(self):
        reviewer = make_user(role=UserRole.CREATOR_REVIEWER)
        NotificationPreference.objects.create(user=reviewer, new_course_assigned=False)

        self.assign(reviewer)

        self.assertFalse(
            Notification.objects.filter(
                receiver=reviewer, title="Course assigned to you"
            ).exists()
        )

    def test_qa_seat_takes_a_qa_reviewer(self):
        self.course.status = CourseStatus.QA_VERIFICATION
        self.course.save(update_fields=["status"])

        refused = self.assign(make_user(role=UserRole.CREATOR_REVIEWER))
        allowed = self.assign(make_user(role=UserRole.QA_REVIEWER))

        self.assertEqual(refused.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(allowed.status_code, status.HTTP_200_OK)
        self.assertEqual(allowed.data["data"]["seat"], ReviewStage.QA)

    def test_a_draft_course_cannot_be_assigned(self):
        self.course.status = CourseStatus.DRAFT
        self.course.save(update_fields=["status"])

        response = self.assign(make_user(role=UserRole.CREATOR_REVIEWER))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_who_may_assign(self):
        reviewer = make_user(role=UserRole.CREATOR_REVIEWER)
        for role, expected in (
            (UserRole.STAFF_APPROVER, status.HTTP_200_OK),
            (UserRole.CREATOR_REVIEWER, status.HTTP_403_FORBIDDEN),
            (UserRole.STAFF_WRITER, status.HTTP_403_FORBIDDEN),
        ):
            with self.subTest(role=role):
                self.client.force_authenticate(make_user(role=role))
                self.assertEqual(self.assign(reviewer).status_code, expected)

    def test_unknown_reviewer_is_404(self):
        response = self.client.post(
            f"{URL}{self.course.id}/assign/",
            {"reviewer_id": "00000000-0000-0000-0000-000000000000"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class AssignableReviewersTests(_AssignBase):
    def url(self):
        return f"{URL}{self.course.id}/assignable-reviewers/"

    def test_lists_eligible_reviewers_with_availability(self):
        available = make_user(role=UserRole.CREATOR_REVIEWER)
        away = make_user(role=UserRole.CREATOR_REVIEWER)
        ReviewerAvailability.objects.create(user=away, is_available=False)
        make_user(role=UserRole.STAFF_VERIFIER)

        rows = {row["id"]: row for row in self.client.get(self.url()).data["data"]}

        self.assertEqual(set(rows), {str(available.id), str(away.id)})
        self.assertTrue(rows[str(available.id)]["is_available"])
        self.assertFalse(rows[str(away.id)]["is_available"])

    def test_query_count_does_not_grow_with_reviewers(self):
        def count():
            with CaptureQueriesContext(connection) as ctx:
                self.client.get(self.url())
            return len(ctx.captured_queries)

        make_user(role=UserRole.CREATOR_REVIEWER)
        one = count()
        for _ in range(4):
            ReviewerAvailability.objects.create(
                user=make_user(role=UserRole.CREATOR_REVIEWER), is_available=True
            )
        self.assertEqual(count(), one)

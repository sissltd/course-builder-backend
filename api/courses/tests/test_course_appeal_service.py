from django.test import TestCase
from rest_framework.exceptions import PermissionDenied, ValidationError

from api.courses.enums import AppealStatus, CourseStatus
from api.courses.services import course_appeal_service
from api.courses.tests.factories import (
    make_course_appeal,
    make_draft_course,
    make_rejected_course,
    make_user,
)
from api.notification.models import Notification
from api.users.enums import UserRole


class SubmitAppealTests(TestCase):
    def test_creates_pending_appeal_and_notifies_admins(self):
        admin = make_user(role=UserRole.ADMIN)
        course = make_rejected_course()

        appeal = course_appeal_service.submit_appeal(
            user=course.creator,
            course=course,
            title="Rejection was unfair",
            email="creator@example.com",
            description="Please reconsider.",
        )

        self.assertEqual(appeal.status, AppealStatus.PENDING)
        self.assertTrue(
            Notification.objects.filter(
                receiver=admin, title="New course-rejection appeal"
            ).exists()
        )

    def test_only_the_owning_creator_can_appeal(self):
        course = make_rejected_course()
        other_creator = make_user()

        with self.assertRaises(PermissionDenied):
            course_appeal_service.submit_appeal(
                user=other_creator,
                course=course,
                title="Rejection was unfair",
                email="creator@example.com",
                description="Please reconsider.",
            )

    def test_raises_when_course_was_never_rejected(self):
        course = make_draft_course()

        with self.assertRaises(ValidationError):
            course_appeal_service.submit_appeal(
                user=course.creator,
                course=course,
                title="Rejection was unfair",
                email="creator@example.com",
                description="Please reconsider.",
            )

    def test_raises_when_course_not_in_draft(self):
        course = make_rejected_course(status=CourseStatus.SUBMITTED)

        with self.assertRaises(ValidationError):
            course_appeal_service.submit_appeal(
                user=course.creator,
                course=course,
                title="Rejection was unfair",
                email="creator@example.com",
                description="Please reconsider.",
            )

    def test_raises_when_an_appeal_is_already_pending(self):
        course = make_rejected_course()
        make_course_appeal(course=course, submitted_by=course.creator)

        with self.assertRaises(ValidationError):
            course_appeal_service.submit_appeal(
                user=course.creator,
                course=course,
                title="Second appeal",
                email="creator@example.com",
                description="Please reconsider again.",
            )


class ApproveAppealTests(TestCase):
    def test_approves_and_resubmits_course_for_review(self):
        course = make_rejected_course()
        appeal = make_course_appeal(course=course, submitted_by=course.creator)
        admin = make_user(role=UserRole.ADMIN)

        result = course_appeal_service.approve_appeal(
            appeal=appeal, actor=admin, notes="Module was confirmed up to date."
        )

        self.assertEqual(result.status, AppealStatus.APPROVED)
        self.assertEqual(result.reviewed_by_id, admin.id)
        course.refresh_from_db()
        self.assertEqual(course.status, CourseStatus.SUBMITTED)
        self.assertTrue(
            Notification.objects.filter(
                receiver=course.creator, title="Appeal approved"
            ).exists()
        )

    def test_raises_when_not_pending(self):
        appeal = make_course_appeal()
        appeal.status = AppealStatus.APPROVED
        appeal.save()

        with self.assertRaises(ValidationError):
            course_appeal_service.approve_appeal(
                appeal=appeal, actor=make_user(role=UserRole.ADMIN)
            )

    def test_creator_reviewer_cannot_approve(self):
        appeal = make_course_appeal()

        with self.assertRaises(PermissionDenied):
            course_appeal_service.approve_appeal(
                appeal=appeal, actor=make_user(role=UserRole.CREATOR_REVIEWER)
            )


class RejectAppealTests(TestCase):
    def test_rejects_and_leaves_course_untouched(self):
        course = make_rejected_course()
        appeal = make_course_appeal(course=course, submitted_by=course.creator)
        admin = make_user(role=UserRole.ADMIN)

        result = course_appeal_service.reject_appeal(
            appeal=appeal, actor=admin, notes="Original rejection stands."
        )

        self.assertEqual(result.status, AppealStatus.REJECTED)
        self.assertEqual(result.decision_notes, "Original rejection stands.")
        course.refresh_from_db()
        self.assertEqual(course.status, CourseStatus.DRAFT)
        self.assertTrue(
            Notification.objects.filter(
                receiver=course.creator, title="Appeal rejected"
            ).exists()
        )

    def test_raises_when_not_pending(self):
        appeal = make_course_appeal()
        appeal.status = AppealStatus.REJECTED
        appeal.save()

        with self.assertRaises(ValidationError):
            course_appeal_service.reject_appeal(
                appeal=appeal, actor=make_user(role=UserRole.ADMIN)
            )

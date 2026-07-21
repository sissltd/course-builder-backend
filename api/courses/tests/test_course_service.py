from decimal import Decimal

from django.test import TestCase
from rest_framework.exceptions import ValidationError

from api.categories.enums import CategoryStatus
from api.courses.enums import CourseStatus
from api.courses.services import course_service
from api.courses.tests.factories import (
    build_compliant_course,
    make_category,
    make_draft_course,
    make_topic,
    make_user,
)
from api.notification.models import Notification


class CreateDraftCourseTests(TestCase):
    def test_creates_draft_with_terms_accepted_at_and_no_price_snapshot(self):
        creator = make_user()
        category = make_category()

        course = course_service.create_draft_course(
            creator=creator,
            category=category,
            title="Intro to X",
            description="d" * 10,
            preview_video_url="https://example.com/p.mp4",
            terms_accepted=True,
        )

        self.assertEqual(course.status, CourseStatus.DRAFT)
        self.assertIsNotNone(course.terms_accepted_at)
        self.assertIsNone(course.creator_price_snapshot)

    def test_raises_when_terms_not_accepted(self):
        creator = make_user()
        category = make_category()

        with self.assertRaises(ValidationError):
            course_service.create_draft_course(
                creator=creator,
                category=category,
                title="X",
                description="d",
                terms_accepted=False,
            )

    def test_raises_when_category_inactive(self):
        creator = make_user()
        category = make_category(status=CategoryStatus.INACTIVE)

        with self.assertRaises(ValidationError):
            course_service.create_draft_course(
                creator=creator,
                category=category,
                title="X",
                description="d",
                terms_accepted=True,
            )

    def test_creates_with_matching_topic(self):
        creator = make_user()
        category = make_category()
        topic = make_topic(category=category)

        course = course_service.create_draft_course(
            creator=creator,
            category=category,
            topic=topic,
            title="X",
            description="d",
            terms_accepted=True,
        )

        self.assertEqual(course.topic_id, topic.id)

    def test_raises_when_topic_does_not_belong_to_category(self):
        creator = make_user()
        category = make_category()
        other_category = make_category()
        topic = make_topic(category=other_category)

        with self.assertRaises(ValidationError):
            course_service.create_draft_course(
                creator=creator,
                category=category,
                topic=topic,
                title="X",
                description="d",
                terms_accepted=True,
            )

    def test_combines_duration_hours_minutes_seconds(self):
        creator = make_user()
        category = make_category()

        course = course_service.create_draft_course(
            creator=creator,
            category=category,
            title="X",
            description="d",
            duration_hours=1,
            duration_minutes=2,
            duration_seconds=3,
            terms_accepted=True,
        )

        self.assertEqual(course.planned_duration_seconds, 1 * 3600 + 2 * 60 + 3)


class SubmitCourseTests(TestCase):
    def test_raises_when_actor_is_not_creator(self):
        course = build_compliant_course()
        other = make_user()

        with self.assertRaises(ValidationError):
            course_service.submit_course(course=course, actor=other)

    def test_raises_when_status_not_draft(self):
        course = build_compliant_course()
        course.status = CourseStatus.SUBMITTED
        course.save()

        with self.assertRaises(ValidationError):
            course_service.submit_course(course=course, actor=course.creator)

    def test_raises_with_structural_failures_when_invalid(self):
        course = make_draft_course()  # no modules at all - fails every structural check

        with self.assertRaises(ValidationError) as ctx:
            course_service.submit_course(course=course, actor=course.creator)

        self.assertIn("structural_standards", ctx.exception.detail)

    def test_happy_path_snapshots_price_transitions_status_and_notifies(self):
        category = make_category(creator_price=Decimal("150.00"))
        course = build_compliant_course(category=category)

        result = course_service.submit_course(course=course, actor=course.creator)

        result.refresh_from_db()
        self.assertEqual(result.status, CourseStatus.SUBMITTED)
        self.assertEqual(result.creator_price_snapshot, Decimal("150.00"))
        self.assertIsNotNone(result.submitted_at)
        self.assertTrue(
            Notification.objects.filter(
                receiver=course.creator, title="Course submitted"
            ).exists()
        )

    def test_price_snapshot_reflects_category_price_at_submit_time_not_creation_time(
        self,
    ):
        category = make_category(creator_price=Decimal("100.00"))
        course = build_compliant_course(category=category)

        category.creator_price = Decimal("200.00")
        category.save()

        result = course_service.submit_course(course=course, actor=course.creator)
        self.assertEqual(result.creator_price_snapshot, Decimal("200.00"))

    def test_price_snapshot_uses_topic_price_over_category_price_when_topic_set(self):
        category = make_category(creator_price=Decimal("100.00"))
        topic = make_topic(category=category, creator_price=Decimal("25.00"))
        course = build_compliant_course(category=category)
        course.topic = topic
        course.save(update_fields=["topic"])

        result = course_service.submit_course(course=course, actor=course.creator)
        self.assertEqual(result.creator_price_snapshot, Decimal("25.00"))


class ClaimForReviewTests(TestCase):
    def test_transitions_submitted_to_in_review(self):
        course = build_compliant_course()
        course_service.submit_course(course=course, actor=course.creator)
        reviewer = make_user()

        result = course_service.claim_for_review(course=course, reviewer=reviewer)
        self.assertEqual(result.status, CourseStatus.IN_REVIEW)

    def test_idempotent_when_already_in_review(self):
        course = build_compliant_course()
        course_service.submit_course(course=course, actor=course.creator)
        reviewer = make_user()
        course_service.claim_for_review(course=course, reviewer=reviewer)

        result = course_service.claim_for_review(course=course, reviewer=reviewer)
        self.assertEqual(result.status, CourseStatus.IN_REVIEW)

    def test_raises_for_other_statuses(self):
        course = build_compliant_course()  # still Draft
        reviewer = make_user()

        with self.assertRaises(ValidationError):
            course_service.claim_for_review(course=course, reviewer=reviewer)


class PublishCourseTests(TestCase):
    def test_raises_when_not_approved(self):
        course = build_compliant_course()

        with self.assertRaises(ValidationError):
            course_service.publish_course(course=course, actor=course.creator)

    def test_happy_path(self):
        course = build_compliant_course()
        course.status = CourseStatus.APPROVED
        course.save()
        admin = make_user()

        result = course_service.publish_course(course=course, actor=admin)
        self.assertEqual(result.status, CourseStatus.PUBLISHED)
        self.assertIsNotNone(result.published_at)

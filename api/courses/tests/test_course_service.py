from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from api.catalog.enums import CategoryStatus
from api.courses.enums import CourseStatus
from api.courses.models import CourseVersion, Lesson, Module, PublishedCourseSnapshot
from api.courses.services import course_service
from api.courses.tests.factories import (
    build_compliant_course,
    make_category,
    make_draft_course,
    make_topic,
    make_user,
)
from api.notification.models import Notification
from api.notification.services import notification_preference_service
from api.users.enums import UserRole


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

    def test_selecting_a_topic_reserves_it_for_the_creator(self):
        creator = make_user()
        category = make_category()
        topic = make_topic(category=category)

        course_service.create_draft_course(
            creator=creator,
            category=category,
            topic=topic,
            title="X",
            description="d",
            terms_accepted=True,
        )

        topic.refresh_from_db()
        self.assertEqual(topic.reserved_by_id, creator.id)
        self.assertIsNotNone(topic.reserved_until)
        self.assertTrue(topic.is_currently_reserved)

    def test_raises_when_topic_reserved_by_someone_else(self):
        creator = make_user()
        other_creator = make_user()
        category = make_category()
        topic = make_topic(category=category)
        course_service.create_draft_course(
            creator=other_creator,
            category=category,
            topic=topic,
            title="First",
            description="d",
            terms_accepted=True,
        )

        with self.assertRaises(ValidationError):
            course_service.create_draft_course(
                creator=creator,
                category=category,
                topic=topic,
                title="Second",
                description="d",
                terms_accepted=True,
            )

    def test_creating_a_second_draft_with_own_reserved_topic_succeeds(self):
        creator = make_user()
        category = make_category()
        topic = make_topic(category=category)
        course_service.create_draft_course(
            creator=creator,
            category=category,
            topic=topic,
            title="First",
            description="d",
            terms_accepted=True,
        )

        course = course_service.create_draft_course(
            creator=creator,
            category=category,
            topic=topic,
            title="Second",
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

    def test_wrong_role_cannot_create(self):
        creator = make_user(role=UserRole.CREATOR_REVIEWER)
        category = make_category()

        with self.assertRaises(PermissionDenied):
            course_service.create_draft_course(
                creator=creator,
                category=category,
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

    def test_wrong_role_cannot_submit(self):
        course = build_compliant_course()
        course.creator.role = UserRole.CREATOR_REVIEWER
        course.creator.save(update_fields=["role"])

        with self.assertRaises(PermissionDenied):
            course_service.submit_course(course=course, actor=course.creator)

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
        reviewer = make_user(role=UserRole.CREATOR_REVIEWER)

        result = course_service.claim_for_review(course=course, reviewer=reviewer)
        self.assertEqual(result.status, CourseStatus.IN_REVIEW)

    def test_idempotent_when_already_in_review(self):
        course = build_compliant_course()
        course_service.submit_course(course=course, actor=course.creator)
        reviewer = make_user(role=UserRole.CREATOR_REVIEWER)
        course_service.claim_for_review(course=course, reviewer=reviewer)

        result = course_service.claim_for_review(course=course, reviewer=reviewer)
        self.assertEqual(result.status, CourseStatus.IN_REVIEW)

    def test_raises_for_other_statuses(self):
        course = build_compliant_course()  # still Draft
        reviewer = make_user(role=UserRole.CREATOR_REVIEWER)

        with self.assertRaises(ValidationError):
            course_service.claim_for_review(course=course, reviewer=reviewer)

    def test_wrong_role_cannot_claim(self):
        course = build_compliant_course()
        course_service.submit_course(course=course, actor=course.creator)
        wrong_role_reviewer = make_user(role=UserRole.COURSE_CREATOR)

        with self.assertRaises(PermissionDenied):
            course_service.claim_for_review(course=course, reviewer=wrong_role_reviewer)


class PublishCourseTests(TestCase):
    def setUp(self):
        self.version = CourseVersion.objects.get_or_create(label="1.0")[0]

    def test_raises_when_not_approved(self):
        course = build_compliant_course()
        admin = make_user(role=UserRole.ADMIN)

        with self.assertRaises(ValidationError):
            course_service.publish_course(course=course, actor=admin)

    def test_happy_path(self):
        course = build_compliant_course()
        course.status = CourseStatus.APPROVED
        course.save()
        admin = make_user(role=UserRole.ADMIN)

        result = course_service.publish_course(course=course, actor=admin)
        self.assertEqual(result.status, CourseStatus.PUBLISHED)
        self.assertIsNotNone(result.published_at)

    def test_wrong_role_cannot_publish(self):
        course = build_compliant_course()
        course.status = CourseStatus.APPROVED
        course.save()

        with self.assertRaises(PermissionDenied):
            course_service.publish_course(course=course, actor=course.creator)

    def test_creates_a_single_v1_0_published_snapshot(self):
        course = build_compliant_course()
        course.status = CourseStatus.APPROVED
        course.save()
        admin = make_user(role=UserRole.ADMIN)

        course_service.publish_course(course=course, actor=admin)

        snapshots = PublishedCourseSnapshot.objects.filter(course=course)
        self.assertEqual(snapshots.count(), 1)
        snapshot = snapshots.first()
        self.assertEqual(snapshot.version.label, "1.0")
        self.assertEqual(snapshot.snapshot["title"], course.title)
        self.assertEqual(len(snapshot.snapshot["modules"]), course.modules.count())
        course.refresh_from_db()
        self.assertEqual(course.version_id, self.version.id)


class RecalculateDurationEstimateTests(TestCase):
    def test_sums_lesson_durations_across_modules(self):
        course = make_draft_course()
        module_a = Module.objects.create(course=course, title="A", order=0)
        module_b = Module.objects.create(course=course, title="B", order=1)
        Lesson.objects.create(module=module_a, title="L1", order=0, duration_minutes=10)
        Lesson.objects.create(module=module_a, title="L2", order=1, duration_minutes=15)
        Lesson.objects.create(module=module_b, title="L3", order=0, duration_minutes=20)

        result = course_service.recalculate_duration_estimate(course=course)

        self.assertEqual(result.duration_estimate_minutes, 45)
        course.refresh_from_db()
        self.assertEqual(course.duration_estimate_minutes, 45)

    def test_returns_zero_for_course_with_no_lessons(self):
        course = make_draft_course()

        result = course_service.recalculate_duration_estimate(course=course)

        self.assertEqual(result.duration_estimate_minutes, 0)


class GetReviewQueueSortingTests(TestCase):
    def setUp(self):
        self.reviewer = make_user(role=UserRole.CREATOR_REVIEWER)
        self.category = make_category()

    def _submitted_course_aged(self, hours_ago):
        course = build_compliant_course(category=self.category)
        course = course_service.submit_course(course=course, actor=course.creator)
        course.submitted_at = timezone.now() - timedelta(hours=hours_ago)
        course.save(update_fields=["submitted_at"])
        return course

    def test_newest_first_reverses_default_ordering(self):
        older = self._submitted_course_aged(hours_ago=10)
        newer = self._submitted_course_aged(hours_ago=1)

        results = list(course_service.get_review_queue(sort_order="NEWEST_FIRST"))

        self.assertEqual(results, [newer, older])

    def test_sla_urgency_ranks_red_then_amber_then_normal(self):
        notification_preference_service.update_preference(
            user=self.reviewer,
            sla_amber_threshold_hours_override=24,
            sla_red_threshold_hours_override=48,
        )
        normal = self._submitted_course_aged(hours_ago=1)
        amber = self._submitted_course_aged(hours_ago=30)
        red = self._submitted_course_aged(hours_ago=60)

        results = list(
            course_service.get_review_queue(
                sort_order="SLA_URGENCY", sla_user=self.reviewer
            )
        )

        self.assertEqual(results, [red, amber, normal])

    def test_track_filter_restricts_to_matching_category(self):
        from api.catalog.enums import TrackPreference

        creator_category = make_category(
            track_preference=TrackPreference.CREATOR_PREFERRED
        )
        ai_category = make_category(track_preference=TrackPreference.AI_PREFERRED)

        creator_course = self._submit_in(creator_category)
        self._submit_in(ai_category)

        results = list(course_service.get_review_queue(track_filter="CREATOR_TRACK"))

        self.assertEqual(results, [creator_course])

    def _submit_in(self, category):
        course = build_compliant_course(category=category)
        return course_service.submit_course(course=course, actor=course.creator)

"""Badges are earned from the real course lifecycle.

Each test drives the same URLs the frontend and reviewers call, then runs the
on-commit callbacks the transition queued, which is when awarding happens.
"""

from decimal import Decimal
from unittest import mock

from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework import status
from rest_framework.test import APITestCase

from api.achievements.enums import AwardSource, BadgeCriterion
from api.achievements.models import CreatorBadge
from api.achievements.services import award_service
from api.achievements.tests.factories import make_badge
from api.courses.enums import CourseStatus
from api.courses.models import CourseVersion
from api.courses.services import course_service
from api.courses.tests.factories import build_compliant_course, make_category, make_user
from api.notification.models import Notification, NotificationPreference
from api.users.enums import UserRole

QUEUE_URL = "/api/v1/review-queue/"


class AwardHookTests(APITestCase):
    def setUp(self):
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.category = make_category(
            creator_price_beginner=Decimal("120.00"),
            creator_price_intermediate=Decimal("120.00"),
            creator_price_advanced=Decimal("120.00"),
        )
        CourseVersion.objects.get_or_create(label="1.0")

    # ── helpers ──────────────────────────────────────────────────────────

    def _holds(self, badge):
        return CreatorBadge.objects.filter(
            badge=badge, creator=self.creator, is_deleted=False
        ).exists()

    def _create_course_over_http(self):
        self.client.force_authenticate(self.creator)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                "/api/v1/courses/",
                {
                    "category": str(self.category.id),
                    "title": "My Course",
                    "description": "d" * 20,
                    "preview_video_url": "https://example.com/p.mp4",
                    "terms_accepted": True,
                },
                format="json",
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        return response

    def _course_through_content_review(self):
        course = course_service.submit_course(
            course=build_compliant_course(creator=self.creator, category=self.category),
            actor=self.creator,
        )
        reviewers = (
            make_user(role=UserRole.CREATOR_REVIEWER),
            make_user(role=UserRole.CREATOR_REVIEWER),
            make_user(role=UserRole.STAFF_VERIFIER),
        )
        with self.captureOnCommitCallbacks(execute=True):
            for reviewer in reviewers:
                self.client.force_authenticate(reviewer)
                self.client.post(f"{QUEUE_URL}{course.id}/claim/")
                response = self.client.post(
                    f"{QUEUE_URL}{course.id}/approve/", {}, format="json"
                )
                self.assertEqual(response.status_code, status.HTTP_200_OK)
        course.refresh_from_db()
        self.assertEqual(course.status, CourseStatus.QA_VERIFICATION)
        return course

    def _register_required_media(self, course):
        self.client.force_authenticate(self.creator)
        video_metadata = {
            "mime_type": "video/mp4",
            "resolution": "1280x720",
            "caption_accuracy_percent": "99.00",
            "audio_lufs": "-16.00",
            "audio_video_drift_ms": 50,
        }
        assets = [
            {
                "lesson": str(lesson.id),
                "kind": "VIDEO",
                "url": f"https://example.com/{lesson.id}.mp4",
                "duration_seconds": 300,
                "subtitle_url": f"https://example.com/{lesson.id}.srt",
                "accessibility": {"captions": True},
                **video_metadata,
            }
            for module in course.modules.all()
            for lesson in module.lessons.all()
        ]
        assets.append(
            {
                "kind": "PREVIEW_VIDEO",
                "url": "https://example.com/preview.mp4",
                "duration_seconds": 60,
                "subtitle_url": "https://example.com/preview.srt",
                "accessibility": {"captions": True},
                **video_metadata,
            }
        )
        assets.append(
            {
                "kind": "THUMBNAIL",
                "url": "https://example.com/thumb.jpg",
                "mime_type": "image/jpeg",
                "resolution": "1280x720",
                "accessibility": {"alt_text": "Course thumbnail"},
            }
        )
        for asset in assets:
            response = self.client.post(
                f"/api/v1/courses/{course.id}/media-assets/", asset, format="json"
            )
            self.assertEqual(
                response.status_code, status.HTTP_201_CREATED, response.data
            )

    def _approve_in_qa(self, course):
        self._register_required_media(course)
        self.client.force_authenticate(make_user(role=UserRole.QA_REVIEWER))
        self.client.post(f"{QUEUE_URL}{course.id}/qa-claim/")
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                f"{QUEUE_URL}{course.id}/qa-approve/", {}, format="json"
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    # ── one hook per criterion ───────────────────────────────────────────

    def test_creating_a_course_awards_a_created_badge(self):
        badge = make_badge(
            criterion=BadgeCriterion.COURSES_CREATED, required_count=1, auto_award=True
        )

        self._create_course_over_http()

        self.assertTrue(self._holds(badge))
        award = CreatorBadge.objects.get(badge=badge, creator=self.creator)
        self.assertEqual(award.source, AwardSource.AUTOMATIC)
        self.assertIsNone(award.awarded_by)
        self.assertTrue(
            Notification.objects.filter(
                receiver=self.creator, title="You earned a badge"
            ).exists()
        )

    def test_passing_content_review_awards_a_reviewed_badge(self):
        reviewed = make_badge(
            criterion=BadgeCriterion.COURSES_REVIEWED, required_count=1, auto_award=True
        )
        approved = make_badge(
            criterion=BadgeCriterion.COURSES_APPROVED, required_count=1, auto_award=True
        )

        self._course_through_content_review()

        self.assertTrue(self._holds(reviewed))
        self.assertFalse(self._holds(approved))

    def test_qa_approval_and_publishing_award_their_badges(self):
        approved = make_badge(
            criterion=BadgeCriterion.COURSES_APPROVED, required_count=1, auto_award=True
        )
        published = make_badge(
            criterion=BadgeCriterion.COURSES_PUBLISHED,
            required_count=1,
            auto_award=True,
        )
        course = self._course_through_content_review()

        self._approve_in_qa(course)

        self.assertTrue(self._holds(approved))
        self.assertFalse(self._holds(published))

        self.client.force_authenticate(make_user(role=UserRole.ADMIN))
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                f"{QUEUE_URL}{course.id}/publish/", {}, format="json"
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertTrue(self._holds(published))

    # ── rules ────────────────────────────────────────────────────────────

    def test_badges_without_auto_award_are_never_earned_automatically(self):
        badge = make_badge(required_count=1, auto_award=False)

        self._create_course_over_http()

        self.assertFalse(self._holds(badge))

    def test_badges_above_the_creators_count_are_not_earned(self):
        badge = make_badge(required_count=2, auto_award=True)

        self._create_course_over_http()
        self.assertFalse(self._holds(badge))

        self._create_course_over_http()
        self.assertTrue(self._holds(badge))

    def test_a_creator_is_never_awarded_the_same_badge_twice(self):
        badge = make_badge(required_count=1, auto_award=True)

        self._create_course_over_http()
        self._create_course_over_http()

        self.assertEqual(
            CreatorBadge.objects.filter(badge=badge, creator=self.creator).count(), 1
        )

    def test_award_is_kept_but_notification_skipped_when_in_app_is_off(self):
        NotificationPreference.objects.create(user=self.creator, in_app_enabled=False)
        badge = make_badge(required_count=1, auto_award=True)

        self._create_course_over_http()

        self.assertTrue(self._holds(badge))
        self.assertFalse(Notification.objects.filter(receiver=self.creator).exists())

    def test_a_failing_evaluation_never_fails_the_course_event(self):
        make_badge(required_count=1, auto_award=True)

        with mock.patch.object(
            award_service, "evaluate_creator", side_effect=RuntimeError("boom")
        ):
            response = self._create_course_over_http()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertFalse(CreatorBadge.objects.exists())

    def test_evaluation_query_count_does_not_grow_with_qualifying_badges(self):
        for _ in range(5):
            course_service.create_draft_course(
                creator=self.creator,
                category=self.category,
                title="Course",
                description="d" * 20,
                terms_accepted=True,
            )

        def count_queries(badges):
            CreatorBadge.objects.all().delete()
            Notification.objects.all().delete()
            with CaptureQueriesContext(connection) as ctx:
                awards = award_service.evaluate_creator(
                    creator_id=self.creator.id, criterion=BadgeCriterion.COURSES_CREATED
                )
            self.assertEqual(len(awards), badges)
            return len(ctx.captured_queries)

        make_badge(required_count=1, auto_award=True)
        one = count_queries(1)
        for required in range(2, 6):
            make_badge(required_count=required, auto_award=True)
        self.assertEqual(count_queries(5), one)

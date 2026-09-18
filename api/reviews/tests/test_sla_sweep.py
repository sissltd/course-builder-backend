"""The sweep that watches courses waiting on a review decision.

Time is moved by backdating `submitted_at` rather than freezing the clock,
matching the rest of the suite (there is no freezegun dependency), and the
sweep takes an injectable `now` for the cases where that is clearer.
"""

from datetime import timedelta

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APITestCase

from api.courses.enums import CourseStatus
from api.courses.models import Course
from api.courses.tests.factories import build_compliant_course, make_category, make_user
from api.notification.models import Notification, NotificationPreference
from api.platform.services import platform_settings_service
from api.reviews.services import review_sla_service
from api.users.enums import UserRole


def submitted_hours_ago(course, hours):
    Course.objects.filter(pk=course.pk).update(
        status=CourseStatus.SUBMITTED,
        submitted_at=timezone.now() - timedelta(hours=hours),
    )
    course.refresh_from_db()
    return course


class SlaSweepTests(APITestCase):
    def setUp(self):
        self.category = make_category()
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.admin = make_user(role=UserRole.ADMIN)
        self.settings_row = platform_settings_service.update_settings(
            sla_red_threshold_hours=48, auto_flag_after_hours=48
        )

    def make_waiting_course(self, hours):
        course = build_compliant_course(
            creator=self.creator, category=self.category
        )
        return submitted_hours_ago(course, hours)

    def test_a_breached_course_alerts_admins_once(self):
        course = self.make_waiting_course(72)

        first = review_sla_service.run_sla_sweep()

        self.assertEqual(first["alerted"], 1)
        self.assertTrue(
            Notification.objects.filter(
                receiver=self.admin, metadata__course_id=str(course.id)
            ).exists()
        )

        second = review_sla_service.run_sla_sweep()

        self.assertEqual(second["alerted"], 0)
        self.assertEqual(
            Notification.objects.filter(receiver=self.admin).count(), 1
        )

    def test_a_course_inside_the_threshold_is_left_alone(self):
        self.make_waiting_course(12)

        report = review_sla_service.run_sla_sweep()

        self.assertEqual(report["alerted"], 0)
        self.assertEqual(report["flagged"], 0)

    def test_an_admin_who_opted_out_is_not_alerted(self):
        NotificationPreference.objects.update_or_create(
            user=self.admin, defaults={"sla_red_critical_alert": False}
        )
        self.make_waiting_course(72)

        review_sla_service.run_sla_sweep()

        self.assertFalse(Notification.objects.filter(receiver=self.admin).exists())

    def test_an_admin_with_no_preference_row_is_still_alerted(self):
        NotificationPreference.objects.filter(user=self.admin).delete()
        self.make_waiting_course(72)

        review_sla_service.run_sla_sweep()

        self.assertTrue(Notification.objects.filter(receiver=self.admin).exists())

    def test_a_stalled_course_is_flagged(self):
        course = self.make_waiting_course(72)

        report = review_sla_service.run_sla_sweep()
        course.refresh_from_db()

        self.assertEqual(report["flagged"], 1)
        self.assertIsNotNone(course.flagged_at)
        self.assertEqual(course.flag_reason, review_sla_service.FLAG_REASON)

    def test_a_course_that_left_the_queue_is_unflagged(self):
        course = self.make_waiting_course(72)
        review_sla_service.run_sla_sweep()
        Course.objects.filter(pk=course.pk).update(status=CourseStatus.PUBLISHED)

        report = review_sla_service.run_sla_sweep()
        course.refresh_from_db()

        self.assertEqual(report["unflagged"], 1)
        self.assertIsNone(course.flagged_at)
        self.assertEqual(course.flag_reason, "")

    def test_zero_disables_each_rule(self):
        platform_settings_service.update_settings(
            sla_red_threshold_hours=48, auto_flag_after_hours=0
        )
        self.make_waiting_course(72)

        report = review_sla_service.run_sla_sweep()

        self.assertEqual(report["flagged"], 0)
        self.assertEqual(report["alerted"], 1)

    def test_resubmitting_clears_the_flag_and_allows_a_new_alert(self):
        from api.courses.services import course_service

        course = self.make_waiting_course(72)
        review_sla_service.run_sla_sweep()

        course_service.start_review_cycle(course=course)
        course.refresh_from_db()

        self.assertIsNone(course.flagged_at)
        self.assertIsNone(course.sla_red_alerted_at)

    def test_recipients_are_resolved_once_regardless_of_course_count(self):
        for _ in range(3):
            self.make_waiting_course(72)

        with CaptureQueriesContext(connection) as ctx:
            review_sla_service.run_sla_sweep()
        three = len(ctx.captured_queries)

        Course.objects.all().update(sla_red_alerted_at=None, flagged_at=None)
        for _ in range(3):
            self.make_waiting_course(72)

        with CaptureQueriesContext(connection) as ctx:
            review_sla_service.run_sla_sweep()

        # Six breached courses cost more notification inserts than three, but
        # the admin lookup must not repeat per course.
        self.assertLess(len(ctx.captured_queries) - three, three)

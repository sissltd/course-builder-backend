"""Queue Behaviour: the design's three track toggles and six sort options."""

from datetime import timedelta

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from api.catalog.enums import TrackPreference
from api.courses.enums import CourseStatus
from api.courses.services import course_service
from api.courses.tests.factories import (
    make_category,
    make_draft_course,
    make_user,
)
from api.users.enums import QueueSortOrder, QueueTrackFilter, UserRole
from api.users.models import QueueBehaviourPreference

URL = "/api/v1/users/me/queue-preferences/"


class SortOrderOptionTests(APITestCase):
    """The dropdown offers exactly what the design lists."""

    def setUp(self):
        self.client.force_authenticate(make_user(role=UserRole.CREATOR_REVIEWER))

    def test_options_match_the_design_dropdown(self):
        self.assertEqual(
            [choice.label for choice in QueueSortOrder],
            [
                "All",
                "Newest First",
                "Oldest First",
                "Last 30 days",
                "Last 7 days",
                "Last 24 hours",
            ],
        )

    def test_every_option_is_accepted(self):
        for choice in QueueSortOrder:
            with self.subTest(option=choice.value):
                response = self.client.patch(
                    URL, {"default_sort_order": choice.value}, format="json"
                )
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(response.data["default_sort_order"], choice.value)

    def test_retired_sla_urgency_is_refused(self):
        response = self.client.patch(
            URL, {"default_sort_order": "SLA_URGENCY"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class TrackToggleTests(APITestCase):
    def setUp(self):
        self.reviewer = make_user(role=UserRole.CREATOR_REVIEWER)
        self.client.force_authenticate(self.reviewer)

    def _preference(self):
        return QueueBehaviourPreference.objects.get(user=self.reviewer)

    def test_defaults_to_showing_both_tracks(self):
        """A new reviewer must not open an empty queue."""

        data = self.client.get(URL).data

        self.assertTrue(data["show_both_track"])
        self.assertEqual(data["effective_track_filter"], QueueTrackFilter.ALL)

    def test_the_three_toggles_are_exposed(self):
        data = self.client.get(URL).data

        for field in ("show_ai_track", "show_creator_track", "show_both_track"):
            self.assertIn(field, data)

    def test_ai_only(self):
        response = self.client.patch(
            URL,
            {
                "show_both_track": False,
                "show_ai_track": True,
                "show_creator_track": False,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["effective_track_filter"], QueueTrackFilter.AI_TRACK
        )

    def test_creator_only(self):
        response = self.client.patch(
            URL,
            {
                "show_both_track": False,
                "show_ai_track": False,
                "show_creator_track": True,
            },
            format="json",
        )

        self.assertEqual(
            response.data["effective_track_filter"], QueueTrackFilter.CREATOR_TRACK
        )

    def test_both_single_toggles_on_is_the_same_as_both(self):
        response = self.client.patch(
            URL,
            {
                "show_both_track": False,
                "show_ai_track": True,
                "show_creator_track": True,
            },
            format="json",
        )

        self.assertEqual(
            response.data["effective_track_filter"], QueueTrackFilter.ALL
        )

    def test_both_toggle_wins_over_the_single_ones(self):
        response = self.client.patch(
            URL,
            {
                "show_both_track": True,
                "show_ai_track": False,
                "show_creator_track": False,
            },
            format="json",
        )

        self.assertEqual(
            response.data["effective_track_filter"], QueueTrackFilter.ALL
        )

    def test_everything_off_is_an_intentionally_empty_queue(self):
        """Not coerced to ALL: the reviewer asked for nothing."""

        response = self.client.patch(
            URL,
            {
                "show_both_track": False,
                "show_ai_track": False,
                "show_creator_track": False,
            },
            format="json",
        )

        self.assertEqual(
            response.data["effective_track_filter"], QueueTrackFilter.NONE
        )


class QueueFilteringTests(APITestCase):
    """The toggles and sort options actually change the queue."""

    def _submitted(self, track, *, days_ago=0):
        category = make_category(track_preference=track)
        course = make_draft_course(category=category)
        course.status = CourseStatus.SUBMITTED
        course.submitted_at = timezone.now() - timedelta(days=days_ago)
        course.save(update_fields=["status", "submitted_at"])
        return course

    def test_ai_filter_excludes_creator_courses(self):
        self._submitted(TrackPreference.AI_PREFERRED)
        self._submitted(TrackPreference.CREATOR_PREFERRED)

        queue = course_service.get_review_queue(
            track_filter=QueueTrackFilter.AI_TRACK
        )

        self.assertEqual(queue.count(), 1)

    def test_none_returns_an_empty_queue(self):
        self._submitted(TrackPreference.AI_PREFERRED)
        self._submitted(TrackPreference.CREATOR_PREFERRED)

        queue = course_service.get_review_queue(track_filter=QueueTrackFilter.NONE)

        self.assertEqual(queue.count(), 0)

    def test_last_7_days_excludes_older_submissions(self):
        self._submitted(TrackPreference.OPEN, days_ago=1)
        self._submitted(TrackPreference.OPEN, days_ago=20)

        queue = course_service.get_review_queue(
            sort_order=QueueSortOrder.LAST_7_DAYS
        )

        self.assertEqual(queue.count(), 1)

    def test_last_24_hours_is_tighter_than_last_30_days(self):
        self._submitted(TrackPreference.OPEN, days_ago=0)
        self._submitted(TrackPreference.OPEN, days_ago=10)

        self.assertEqual(
            course_service.get_review_queue(
                sort_order=QueueSortOrder.LAST_24_HOURS
            ).count(),
            1,
        )
        self.assertEqual(
            course_service.get_review_queue(
                sort_order=QueueSortOrder.LAST_30_DAYS
            ).count(),
            2,
        )

    def test_all_applies_no_date_filter(self):
        self._submitted(TrackPreference.OPEN, days_ago=0)
        self._submitted(TrackPreference.OPEN, days_ago=200)

        self.assertEqual(
            course_service.get_review_queue(sort_order=QueueSortOrder.ALL).count(), 2
        )

    def test_newest_first_reverses_the_order(self):
        old = self._submitted(TrackPreference.OPEN, days_ago=10)
        new = self._submitted(TrackPreference.OPEN, days_ago=1)

        newest = list(
            course_service.get_review_queue(sort_order=QueueSortOrder.NEWEST_FIRST)
        )
        oldest = list(
            course_service.get_review_queue(sort_order=QueueSortOrder.OLDEST_FIRST)
        )

        self.assertEqual(newest[0].id, new.id)
        self.assertEqual(oldest[0].id, old.id)

    def test_sla_urgency_still_works_at_the_service_layer(self):
        """Retired from the dropdown, not deleted from the service."""

        self._submitted(TrackPreference.OPEN, days_ago=10)

        queue = course_service.get_review_queue(
            sort_order="SLA_URGENCY",
            sla_user=make_user(role=UserRole.CREATOR_REVIEWER),
        )

        self.assertEqual(queue.count(), 1)

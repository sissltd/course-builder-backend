"""Reviewer dashboard tiles and the Activity Overview chart series.

The design leads with three tiles (Courses Reviewed / Courses in Queue /
Escalation resolved) and a per-day chart with an All time / Today / This
week / This month selector. An escalation is a CourseAppeal - PRD Section
12 routes a disputed rejection to a senior reviewer.
"""

from datetime import timedelta

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from api.courses.enums import AppealStatus, CourseStatus
from api.courses.tests.factories import (
    make_course_appeal,
    make_draft_course,
    make_user,
)
from api.reviews.enums import ReviewActionType, ReviewStage
from api.reviews.models import ReviewAction
from api.users.enums import UserRole

OVERVIEW = "/api/v1/reviewer/overview/"
ACTIVITY = "/api/v1/reviewer/activity-overview/"


def _decide(reviewer, course, action, when=None):
    row = ReviewAction.objects.create(
        course=course, reviewer=reviewer, action=action, stage=ReviewStage.CONTENT
    )
    if when is not None:
        ReviewAction.objects.filter(pk=row.pk).update(created_datetime=when)
    return row


def _resolve_appeal(reviewer, status_value=AppealStatus.APPROVED, when=None):
    appeal = make_course_appeal()
    appeal.status = status_value
    appeal.reviewed_by = reviewer
    appeal.reviewed_at = timezone.now()
    appeal.save(update_fields=["status", "reviewed_by", "reviewed_at"])
    if when is not None:
        type(appeal).objects.filter(pk=appeal.pk).update(created_datetime=when)
    return appeal


class ReviewerDashboardTileTests(APITestCase):
    def setUp(self):
        self.reviewer = make_user(role=UserRole.CREATOR_REVIEWER)
        self.client.force_authenticate(self.reviewer)

    def test_tiles_are_present_alongside_the_legacy_shape(self):
        response = self.client.get(OVERVIEW)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        for key in ("courses_reviewed", "courses_in_queue", "escalations_resolved"):
            self.assertIn(key, response.data)
        # The pre-existing keys must survive for clients already using them.
        self.assertIn("queue", response.data)
        self.assertIn("my_decisions", response.data)

    def test_courses_reviewed_counts_both_decisions(self):
        _decide(self.reviewer, make_draft_course(), ReviewActionType.APPROVE)
        _decide(self.reviewer, make_draft_course(), ReviewActionType.APPROVE)
        _decide(self.reviewer, make_draft_course(), ReviewActionType.REJECT)

        response = self.client.get(OVERVIEW)

        self.assertEqual(response.data["courses_reviewed"], 3)

    def test_courses_reviewed_is_scoped_to_the_caller(self):
        other = make_user(role=UserRole.CREATOR_REVIEWER)
        _decide(other, make_draft_course(), ReviewActionType.APPROVE)

        response = self.client.get(OVERVIEW)

        self.assertEqual(response.data["courses_reviewed"], 0)

    def test_courses_in_queue_sums_reviewable_states(self):
        for state in (CourseStatus.SUBMITTED, CourseStatus.IN_REVIEW):
            course = make_draft_course()
            course.status = state
            course.save(update_fields=["status"])
        published = make_draft_course()
        published.status = CourseStatus.PUBLISHED
        published.save(update_fields=["status"])

        response = self.client.get(OVERVIEW)

        self.assertEqual(response.data["courses_in_queue"], 2)

    def test_escalations_resolved_counts_decided_appeals_only(self):
        _resolve_appeal(self.reviewer, AppealStatus.APPROVED)
        _resolve_appeal(self.reviewer, AppealStatus.REJECTED)
        # A pending appeal assigned to nobody is not "resolved".
        make_course_appeal()

        response = self.client.get(OVERVIEW)

        self.assertEqual(response.data["escalations_resolved"], 2)

    def test_escalations_resolved_is_scoped_to_the_caller(self):
        other = make_user(role=UserRole.CREATOR_REVIEWER)
        _resolve_appeal(other)

        response = self.client.get(OVERVIEW)

        self.assertEqual(response.data["escalations_resolved"], 0)


class ReviewerActivityOverviewTests(APITestCase):
    def setUp(self):
        self.reviewer = make_user(role=UserRole.CREATOR_REVIEWER)
        self.client.force_authenticate(self.reviewer)

    def test_requires_authentication(self):
        self.client.force_authenticate(None)

        self.assertEqual(
            self.client.get(ACTIVITY).status_code, status.HTTP_401_UNAUTHORIZED
        )

    def test_creator_role_is_forbidden(self):
        self.client.force_authenticate(make_user(role=UserRole.COURSE_CREATOR))

        self.assertEqual(
            self.client.get(ACTIVITY).status_code, status.HTTP_403_FORBIDDEN
        )

    def test_today_series_counts_all_three_signals(self):
        _decide(self.reviewer, make_draft_course(), ReviewActionType.APPROVE)
        _decide(self.reviewer, make_draft_course(), ReviewActionType.REJECT)
        _resolve_appeal(self.reviewer)

        response = self.client.get(f"{ACTIVITY}?period=today")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["period"], "today")
        self.assertEqual(len(response.data["series"]), 1)
        point = response.data["series"][0]
        self.assertEqual(point["approved"], 1)
        self.assertEqual(point["rejected"], 1)
        self.assertEqual(point["escalated"], 1)

    def test_totals_match_the_series(self):
        _decide(self.reviewer, make_draft_course(), ReviewActionType.APPROVE)
        _decide(self.reviewer, make_draft_course(), ReviewActionType.APPROVE)

        data = self.client.get(f"{ACTIVITY}?period=this_month").data

        self.assertEqual(data["totals"]["approved"], 2)
        self.assertEqual(
            data["totals"]["approved"],
            sum(row["approved"] for row in data["series"]),
        )

    def test_every_day_in_range_is_present_including_zeroes(self):
        """The chart needs a stable x-axis; gaps must not be the client's job."""

        data = self.client.get(f"{ACTIVITY}?period=this_month").data

        today = timezone.localdate()
        expected_days = today.day  # 1st of the month through today
        self.assertEqual(len(data["series"]), expected_days)
        self.assertTrue(all(row["approved"] == 0 for row in data["series"]))

    def test_series_is_ordered_oldest_first(self):
        dates = [
            row["date"] for row in self.client.get(f"{ACTIVITY}?period=this_month").data["series"]
        ]

        self.assertEqual(dates, sorted(dates))

    def test_scoped_to_the_calling_reviewer(self):
        other = make_user(role=UserRole.CREATOR_REVIEWER)
        _decide(other, make_draft_course(), ReviewActionType.APPROVE)

        data = self.client.get(f"{ACTIVITY}?period=today").data

        self.assertEqual(data["totals"]["approved"], 0)

    def test_older_activity_is_excluded_from_a_narrow_period(self):
        old = timezone.now() - timedelta(days=40)
        _decide(self.reviewer, make_draft_course(), ReviewActionType.APPROVE, when=old)

        today_data = self.client.get(f"{ACTIVITY}?period=today").data

        self.assertEqual(today_data["totals"]["approved"], 0)

    def test_all_time_reaches_back_to_first_activity(self):
        old = timezone.now() - timedelta(days=40)
        _decide(self.reviewer, make_draft_course(), ReviewActionType.APPROVE, when=old)

        data = self.client.get(f"{ACTIVITY}?period=all_time").data

        self.assertEqual(data["totals"]["approved"], 1)
        self.assertGreaterEqual(len(data["series"]), 41)

    def test_all_time_with_no_activity_returns_a_single_zero_day(self):
        data = self.client.get(f"{ACTIVITY}?period=all_time").data

        self.assertEqual(len(data["series"]), 1)
        self.assertEqual(data["totals"], {"escalated": 0, "approved": 0, "rejected": 0})

    def test_unknown_period_falls_back_to_today(self):
        data = self.client.get(f"{ACTIVITY}?period=nonsense").data

        self.assertEqual(data["period"], "today")

    def test_defaults_to_today_when_omitted(self):
        self.assertEqual(self.client.get(ACTIVITY).data["period"], "today")


class MyAuditLogExportTests(APITestCase):
    """Data and Privacy screen's second download button."""

    URL = "/api/v1/users/me/audit-log/export/"

    def setUp(self):
        self.reviewer = make_user(role=UserRole.CREATOR_REVIEWER)

    def test_requires_authentication(self):
        self.assertEqual(
            self.client.get(self.URL).status_code, status.HTTP_401_UNAUTHORIZED
        )

    def test_returns_csv_scoped_to_the_caller(self):
        from shared.audit.models import AuditLog

        other = make_user(role=UserRole.CREATOR_REVIEWER)
        AuditLog.objects.create(
            event=AuditLog.Event.OTP_VERIFIED, email=self.reviewer.email
        )
        AuditLog.objects.create(event=AuditLog.Event.OTP_FAILED, email=other.email)

        self.client.force_authenticate(self.reviewer)
        response = self.client.get(self.URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn("attachment;", response["Content-Disposition"])

        body = b"".join(response.streaming_content).decode()
        self.assertIn("event,email", body)
        self.assertIn(self.reviewer.email, body)
        self.assertNotIn(other.email, body)


class AssignedTrackTests(APITestCase):
    """The Account settings screen shows an admin-assigned track that the
    reviewer cannot change themselves."""

    def setUp(self):
        from api.users.enums import QueueTrackFilter

        self.tracks = QueueTrackFilter
        self.reviewer = make_user(role=UserRole.CREATOR_REVIEWER)
        self.admin = make_user(role=UserRole.ADMIN)

    def _assign(self, value):
        return self.client.post(
            f"/api/v1/users/admin/{self.reviewer.id}/assign-track/",
            {"assigned_track": value},
            format="json",
        )

    def test_defaults_to_unassigned(self):
        self.client.force_authenticate(self.reviewer)

        response = self.client.get("/api/v1/users/me/")

        self.assertIsNone(response.data["assigned_track"])

    def test_admin_assigns_and_reviewer_reads_it(self):
        self.client.force_authenticate(self.admin)
        assigned = self._assign(self.tracks.CREATOR_TRACK.value)

        self.assertEqual(assigned.status_code, status.HTTP_200_OK)
        self.assertEqual(
            assigned.data["assigned_track"], self.tracks.CREATOR_TRACK.value
        )

        # force_authenticate pins the exact object passed, and MeView returns
        # request.user - so refresh or the read sees a stale instance.
        self.reviewer.refresh_from_db()
        self.client.force_authenticate(self.reviewer)
        me = self.client.get("/api/v1/users/me/")

        self.assertEqual(me.data["assigned_track"], self.tracks.CREATOR_TRACK.value)

    def test_assignment_can_be_cleared(self):
        self.client.force_authenticate(self.admin)
        self._assign(self.tracks.AI_TRACK.value)
        self.reviewer.refresh_from_db()
        self.assertEqual(self.reviewer.assigned_track, self.tracks.AI_TRACK.value)

        cleared = self._assign(None)

        self.assertEqual(cleared.status_code, status.HTTP_200_OK)
        self.reviewer.refresh_from_db()
        self.assertIsNone(self.reviewer.assigned_track)

    def test_reviewer_cannot_assign_their_own_track(self):
        self.client.force_authenticate(self.reviewer)

        response = self._assign(self.tracks.AI_TRACK.value)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.reviewer.refresh_from_db()
        self.assertIsNone(self.reviewer.assigned_track)

    def test_reviewer_cannot_patch_it_through_their_own_profile(self):
        """It is read-only on /users/me/ - the whole point of 'set by admin'."""

        self.client.force_authenticate(self.reviewer)

        self.client.patch(
            "/api/v1/users/me/",
            {"assigned_track": self.tracks.AI_TRACK.value},
            format="json",
        )

        self.reviewer.refresh_from_db()
        self.assertIsNone(self.reviewer.assigned_track)

    def test_unknown_track_is_rejected(self):
        self.client.force_authenticate(self.admin)

        response = self._assign("NOT_A_TRACK")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

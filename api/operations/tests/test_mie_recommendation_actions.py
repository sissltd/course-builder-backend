"""The Recommendations screen's working parts: columns, filters, decisions.

The table draws topic, category, difficulty, demand score and monthly
searches, filters on all of them, and decides ideas one row at a time or a
checkbox selection at a time. Everything here goes through the URLs the
screen calls.
"""

from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework import status
from rest_framework.test import APITestCase

from api.courses.enums import CourseStatus, DifficultyLevel
from api.courses.tests.factories import make_category, make_draft_course, make_user
from api.mie.enums import SubmissionStatus
from api.mie.models import CourseSubmission, WebhookEvent
from api.mie.services.submission_admin_service import BULK_DECISION_LIMIT
from api.mie.tests.factories import (
    make_approved_developer,
    make_decided_submission,
    make_rejection_reason,
    make_submission,
)
from api.users.enums import UserRole
from api.users.models import UserActivityLog

URL = "/api/v1/admin/mie-recommendations/"
BULK_URL = f"{URL}decisions/"


def approve_url(submission):
    return f"{URL}{submission.id}/approve/"


def reject_url(submission):
    return f"{URL}{submission.id}/reject/"


class MieRecommendationRowTests(APITestCase):
    """Every column the design draws has to arrive on the row."""

    def setUp(self):
        self.client.force_authenticate(make_user(role=UserRole.STAFF_WRITER))
        self.developer, _key = make_approved_developer()
        self.category = make_category(name="Software Engineering")

    def test_row_carries_the_columns_the_table_draws(self):
        submission = make_submission(
            developer=self.developer,
            title="Introduction to Software design",
            category=self.category,
            difficulty_level=DifficultyLevel.ADVANCED,
            searches_per_month=23000,
            description="Principles, methods and practices of software design.",
            demand_score=69,
        )

        row = self.client.get(URL).data["data"]["results"][0]

        self.assertEqual(row["id"], str(submission.id))
        self.assertEqual(row["title"], "Introduction to Software design")
        self.assertEqual(row["category"]["name"], "Software Engineering")
        self.assertEqual(row["difficulty_level"], DifficultyLevel.ADVANCED)
        self.assertEqual(row["searches_per_month"], 23000)
        self.assertEqual(row["demand_score"], 69)
        self.assertEqual(
            row["description"],
            "Principles, methods and practices of software design.",
        )
        self.assertEqual(row["status"], SubmissionStatus.PENDING_REVIEW)

    def test_row_survives_an_idea_that_gave_none_of_it(self):
        """Partners submitting only a title predate these fields entirely."""

        make_submission(developer=self.developer, title="Bare idea")

        row = self.client.get(URL).data["data"]["results"][0]

        self.assertIsNone(row["category"])
        self.assertEqual(row["difficulty_level"], "")
        self.assertIsNone(row["searches_per_month"])
        self.assertEqual(row["description"], "")


class MieRecommendationFilterTests(APITestCase):
    """The toolbar: search, category, difficulty, score band, date range."""

    def setUp(self):
        self.client.force_authenticate(make_user(role=UserRole.STAFF_WRITER))
        self.developer, _key = make_approved_developer()
        self.engineering = make_category(name="Software Engineering")
        self.leadership = make_category(name="Leadership")

        self.rust = make_submission(
            developer=self.developer,
            title="Rust for backend engineers",
            category=self.engineering,
            difficulty_level=DifficultyLevel.ADVANCED,
            demand_score=90,
        )
        self.managing = make_submission(
            developer=self.developer,
            title="Managing a first team",
            category=self.leadership,
            difficulty_level=DifficultyLevel.BEGINNER,
            demand_score=20,
        )
        self.unscored = make_submission(
            developer=self.developer, title="Unscored idea"
        )

    def _titles(self, query):
        response = self.client.get(f"{URL}{query}")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return {row["title"] for row in response.data["data"]["results"]}

    def test_search_matches_the_title(self):
        self.assertEqual(self._titles("?search=rust"), {"Rust for backend engineers"})

    def test_category_narrows_to_one_category(self):
        self.assertEqual(
            self._titles(f"?category={self.leadership.id}"), {"Managing a first team"}
        )

    def test_difficulty_narrows_to_one_level(self):
        self.assertEqual(
            self._titles(f"?difficulty_level={DifficultyLevel.ADVANCED}"),
            {"Rust for backend engineers"},
        )

    def test_unknown_difficulty_is_refused_rather_than_ignored(self):
        response = self.client.get(f"{URL}?difficulty_level=EXPERT")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_min_demand_score_drops_lower_and_unscored_ideas(self):
        self.assertEqual(
            self._titles("?min_demand_score=50"), {"Rust for backend engineers"}
        )

    def test_date_range_bounds_the_arrival_time(self):
        # "Z" rather than isoformat()'s "+00:00": an unencoded plus decodes to
        # a space in a query string, which is not a timestamp.
        arrived = self.rust.created_datetime.isoformat().replace("+00:00", "Z")

        self.assertIn(
            "Rust for backend engineers", self._titles(f"?submitted_after={arrived}")
        )
        self.assertEqual(self._titles("?submitted_before=2020-01-01T00:00:00Z"), set())

    def test_an_unparseable_date_is_refused(self):
        response = self.client.get(f"{URL}?submitted_after=last%20tuesday")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_totals_describe_the_filtered_view(self):
        data = self.client.get(f"{URL}?category={self.engineering.id}").data["data"]

        self.assertEqual(data["pending_total"], 1)
        self.assertEqual(data["scored_total"], 1)

    def test_listing_cost_does_not_grow_with_the_number_of_rows(self):
        """Every added row carries a category and its own developer, so a
        missing select_related shows up as a query per row rather than
        hiding behind nulls that never traverse the relation."""

        with CaptureQueriesContext(connection) as three_rows:
            self.client.get(URL)

        for index in range(5):
            developer, _key = make_approved_developer()
            make_submission(
                developer=developer,
                title=f"Extra idea {index}",
                category=self.engineering,
                demand_score=index,
            )

        with CaptureQueriesContext(connection) as eight_rows:
            self.client.get(URL)

        self.assertEqual(len(three_rows), len(eight_rows))


class MieRecommendationDecisionTests(APITestCase):
    """One row at a time, from the Approve and Reject buttons."""

    def setUp(self):
        self.writer = make_user(role=UserRole.STAFF_WRITER)
        self.client.force_authenticate(self.writer)
        self.developer, _key = make_approved_developer()
        self.reason = make_rejection_reason(label="Duplicate of existing catalog")
        self.submission = make_submission(
            developer=self.developer, title="Introduction to Software design"
        )

    def test_writer_approves_an_idea(self):
        response = self.client.post(approve_url(self.submission), format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])
        self.assertEqual(response.data["message"], "Topic successfully approved.")
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.status, SubmissionStatus.APPROVED)
        self.assertEqual(self.submission.decided_by, self.writer)

    def test_approval_notifies_the_submitter_and_is_audited(self):
        self.client.post(approve_url(self.submission), format="json")

        self.assertTrue(
            WebhookEvent.objects.filter(
                submission=self.submission, event_type="SUBMISSION_APPROVED"
            ).exists()
        )
        self.assertTrue(
            UserActivityLog.objects.filter(
                user=self.writer, action="COURSE_APPROVED"
            ).exists()
        )

    def test_rejection_records_the_reason(self):
        response = self.client.post(
            reject_url(self.submission),
            {
                "rejection_reason": "Duplicate of existing catalog",
                "rejection_note": "Covered by the live course.",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.status, SubmissionStatus.REJECTED)
        self.assertEqual(self.submission.rejection_reason, self.reason)

    def test_rejection_without_a_reason_is_refused(self):
        response = self.client.post(reject_url(self.submission), {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.status, SubmissionStatus.PENDING_REVIEW)

    def test_unknown_rejection_reason_is_not_found(self):
        response = self.client.post(
            reject_url(self.submission),
            {"rejection_reason": "No such reason"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_unknown_idea_is_not_found(self):
        response = self.client.post(
            f"{URL}0d1c7b2e-6f5a-4a3f-9a2b-1f4e8c9d0a11/approve/", format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_only_deciders_may_decide(self):
        for role, expected in (
            (UserRole.SUPER_ADMIN, status.HTTP_200_OK),
            (UserRole.ADMIN, status.HTTP_403_FORBIDDEN),
            (UserRole.STAFF_APPROVER, status.HTTP_403_FORBIDDEN),
            (UserRole.COURSE_CREATOR, status.HTTP_403_FORBIDDEN),
        ):
            with self.subTest(role=role):
                submission = make_submission(developer=self.developer)
                self.client.force_authenticate(make_user(role=role))

                response = self.client.post(approve_url(submission), format="json")

                self.assertEqual(response.status_code, expected)

        self.client.force_authenticate(None)
        self.assertEqual(
            self.client.post(approve_url(self.submission), format="json").status_code,
            status.HTTP_401_UNAUTHORIZED,
        )


class MieRecommendationBulkDecisionTests(APITestCase):
    """The checkbox selection and its bulk bar."""

    def setUp(self):
        self.writer = make_user(role=UserRole.STAFF_WRITER)
        self.client.force_authenticate(self.writer)
        self.developer, _key = make_approved_developer()
        self.reason = make_rejection_reason(label="Duplicate of existing catalog")

    def _ideas(self, count):
        return [
            make_submission(developer=self.developer, title=f"Idea {index}")
            for index in range(count)
        ]

    def test_bulk_approves_the_whole_selection(self):
        ideas = self._ideas(5)

        response = self.client.post(
            BULK_URL,
            {"ids": [str(idea.id) for idea in ideas], "action": "approve"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["message"], "You have approved 5 topics.")
        self.assertEqual(response.data["data"]["decided"], 5)
        self.assertEqual(
            CourseSubmission.objects.filter(
                status=SubmissionStatus.APPROVED
            ).count(),
            5,
        )
        # Each idea still gets its own webhook and audit row.
        self.assertEqual(
            WebhookEvent.objects.filter(event_type="SUBMISSION_APPROVED").count(), 5
        )
        self.assertEqual(
            UserActivityLog.objects.filter(action="COURSE_APPROVED").count(), 5
        )

    def test_one_reason_applies_to_a_bulk_rejection(self):
        ideas = self._ideas(3)

        response = self.client.post(
            BULK_URL,
            {
                "ids": [str(idea.id) for idea in ideas],
                "action": "reject",
                "rejection_reason": "Duplicate of existing catalog",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            CourseSubmission.objects.filter(rejection_reason=self.reason).count(), 3
        )

    def test_bulk_rejection_leaves_a_linked_published_course_untouched(self):
        course = make_draft_course(status=CourseStatus.PUBLISHED)
        linked = make_decided_submission(
            developer=self.developer, approved=True, resulting_course=course
        )
        unlinked = make_submission(developer=self.developer, title="Unlinked idea")

        response = self.client.post(
            BULK_URL,
            {
                "ids": [str(linked.id), str(unlinked.id)],
                "action": "reject",
                "rejection_reason": "Duplicate of existing catalog",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        course.refresh_from_db()
        linked.refresh_from_db()
        self.assertEqual(course.status, CourseStatus.PUBLISHED)
        self.assertEqual(linked.resulting_course, course)
        linked_audit = UserActivityLog.objects.get(object_id=str(linked.id))
        unlinked_audit = UserActivityLog.objects.get(object_id=str(unlinked.id))
        self.assertEqual(linked_audit.details["resulting_course_id"], str(course.id))
        self.assertNotIn("resulting_course_id", unlinked_audit.details)

    def test_bulk_rejection_needs_a_reason(self):
        ideas = self._ideas(2)

        response = self.client.post(
            BULK_URL,
            {"ids": [str(idea.id) for idea in ideas], "action": "reject"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            CourseSubmission.objects.filter(
                status=SubmissionStatus.PENDING_REVIEW
            ).count(),
            2,
        )

    def test_an_unknown_id_fails_the_batch_without_writing(self):
        ideas = self._ideas(2)

        response = self.client.post(
            BULK_URL,
            {
                "ids": [
                    str(ideas[0].id),
                    "0d1c7b2e-6f5a-4a3f-9a2b-1f4e8c9d0a11",
                ],
                "action": "approve",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(
            CourseSubmission.objects.filter(
                status=SubmissionStatus.PENDING_REVIEW
            ).count(),
            2,
        )

    def test_selection_larger_than_the_cap_is_refused(self):
        response = self.client.post(
            BULK_URL,
            {
                "ids": [
                    "0d1c7b2e-6f5a-4a3f-9a2b-1f4e8c9d0a11"
                ]
                * (BULK_DECISION_LIMIT + 1),
                "action": "approve",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_only_deciders_may_bulk_decide(self):
        ideas = self._ideas(1)
        body = {"ids": [str(ideas[0].id)], "action": "approve"}

        for role, expected in (
            (UserRole.ADMIN, status.HTTP_403_FORBIDDEN),
            (UserRole.COURSE_CREATOR, status.HTTP_403_FORBIDDEN),
        ):
            with self.subTest(role=role):
                self.client.force_authenticate(make_user(role=role))
                self.assertEqual(
                    self.client.post(BULK_URL, body, format="json").status_code,
                    expected,
                )

    def test_bulk_cost_does_not_grow_with_the_selection(self):
        """Validate-then-batch: one UPDATE and two INSERTs either way."""

        one = self._ideas(1)
        with CaptureQueriesContext(connection) as single:
            self.client.post(
                BULK_URL,
                {"ids": [str(one[0].id)], "action": "approve"},
                format="json",
            )

        five = self._ideas(5)
        with CaptureQueriesContext(connection) as batch:
            self.client.post(
                BULK_URL,
                {"ids": [str(idea.id) for idea in five], "action": "approve"},
                format="json",
            )

        self.assertEqual(len(single), len(batch))

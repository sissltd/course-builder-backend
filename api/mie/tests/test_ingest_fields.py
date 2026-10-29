"""The fields a submitter can send beyond the title.

Each one is lifted into its own column so the reviewer's queue can show,
sort and filter on it, while the payload keeps the body verbatim. Category
is the only one that resolves against platform data.
"""

from rest_framework import status
from rest_framework.test import APITestCase

from api.catalog.enums import CategoryStatus
from api.courses.enums import DifficultyLevel
from api.courses.tests.factories import make_category
from api.mie.models import CourseSubmission
from api.mie.models.course_submission import DESCRIPTION_MAX_LENGTH
from api.mie.tests.factories import make_approved_developer

INGEST_URL = "/api/v1/mie/v1/submissions/"


class IngestExtraFieldsTests(APITestCase):
    def setUp(self):
        self.account, self.raw_key = make_approved_developer()
        self.category = make_category(name="Software Engineering")

    def _post(self, payload):
        return self.client.post(
            INGEST_URL, payload, format="json", HTTP_X_MIE_API_KEY=self.raw_key
        )

    def test_every_field_is_stored_on_its_own_column(self):
        response = self._post(
            {
                "title": "Kubernetes Security Hardening",
                "description": "Hardening clusters for DevOps engineers.",
                "category": "Software Engineering",
                "difficulty_level": "ADVANCED",
                "searches_per_month": 23000,
            }
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        submission = CourseSubmission.objects.get(id=response.data["id"])
        self.assertEqual(submission.description, "Hardening clusters for DevOps engineers.")
        self.assertEqual(submission.category, self.category)
        self.assertEqual(submission.difficulty_level, DifficultyLevel.ADVANCED)
        self.assertEqual(submission.searches_per_month, 23000)
        # The body is still kept exactly as it arrived.
        self.assertEqual(submission.payload["category"], "Software Engineering")

    def test_category_resolves_by_slug_and_ignores_case(self):
        response = self._post(
            {"title": "Slug match", "category": self.category.slug.upper()}
        )

        submission = CourseSubmission.objects.get(id=response.data["id"])
        self.assertEqual(submission.category, self.category)

    def test_unmatched_category_is_filed_without_one(self):
        """Not an error: the reviewer can still see what was meant and set
        the category themselves."""

        response = self._post({"title": "No such category", "category": "Basket weaving"})

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        submission = CourseSubmission.objects.get(id=response.data["id"])
        self.assertIsNone(submission.category)
        self.assertEqual(submission.payload["category"], "Basket weaving")

    def test_archived_categories_do_not_match(self):
        archived = make_category(name="Retired topic", status=CategoryStatus.ARCHIVED)

        response = self._post({"title": "Archived match", "category": archived.name})

        submission = CourseSubmission.objects.get(id=response.data["id"])
        self.assertIsNone(submission.category)

    def test_omitting_them_all_still_works(self):
        response = self._post({"title": "Bare idea"})

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        submission = CourseSubmission.objects.get(id=response.data["id"])
        self.assertEqual(submission.description, "")
        self.assertIsNone(submission.category)
        self.assertEqual(submission.difficulty_level, "")
        self.assertIsNone(submission.searches_per_month)

    def test_unknown_difficulty_is_refused(self):
        response = self._post({"title": "Bad difficulty", "difficulty_level": "EXPERT"})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(CourseSubmission.objects.filter(title="Bad difficulty").exists())

    def test_searches_per_month_must_be_a_whole_number(self):
        for value in (-1, "23k", True):
            with self.subTest(value=value):
                response = self._post(
                    {"title": f"Bad volume {value}", "searches_per_month": value}
                )

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_description_longer_than_the_cap_is_refused(self):
        response = self._post(
            {"title": "Too much", "description": "x" * (DESCRIPTION_MAX_LENGTH + 1)}
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

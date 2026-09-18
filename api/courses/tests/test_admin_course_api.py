from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from api.courses.enums import CourseStatus
from api.courses.models import Course
from api.courses.tests.factories import make_category, make_draft_course, make_user
from api.reviews.enums import ReviewStage
from api.users.enums import UserRole


class AdminCourseApiTests(APITestCase):
    def setUp(self):
        self.admin = make_user(role=UserRole.ADMIN)
        self.reviewer = make_user(role=UserRole.CREATOR_REVIEWER)
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.category = make_category()
        self.draft = make_draft_course(
            creator=self.creator,
            category=self.category,
            title="Python for Data Analysis",
        )
        self.submitted = make_draft_course(
            creator=self.creator,
            category=self.category,
            title="Advanced Excel",
            status=CourseStatus.SUBMITTED,
        )

    def test_admin_list_contains_courses_in_every_status(self):
        self.client.force_authenticate(self.admin)

        response = self.client.get("/api/v1/admin/courses/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = {item["id"] for item in response.data["data"]["results"]}
        self.assertEqual(ids, {str(self.draft.id), str(self.submitted.id)})

    def _flag(self, course, reason="No review decision within the configured window."):
        Course.objects.filter(pk=course.pk).update(
            flagged_at=timezone.now(), flag_reason=reason
        )

    def test_flagged_filter_lists_only_flagged_courses(self):
        self._flag(self.submitted)
        self.client.force_authenticate(self.admin)

        flagged = self.client.get("/api/v1/admin/courses/", {"flagged": "true"})
        unflagged = self.client.get("/api/v1/admin/courses/", {"flagged": "false"})

        self.assertEqual(flagged.status_code, status.HTTP_200_OK)
        self.assertEqual(
            {item["id"] for item in flagged.data["data"]["results"]},
            {str(self.submitted.id)},
        )
        self.assertEqual(
            {item["id"] for item in unflagged.data["data"]["results"]},
            {str(self.draft.id)},
        )

    def test_list_rows_carry_the_flag_fields(self):
        self._flag(self.submitted, reason="Stalled")
        self.client.force_authenticate(self.admin)

        response = self.client.get("/api/v1/admin/courses/")

        rows = {item["id"]: item for item in response.data["data"]["results"]}
        flagged_row = rows[str(self.submitted.id)]
        self.assertIsNotNone(flagged_row["flagged_at"])
        self.assertEqual(flagged_row["flag_reason"], "Stalled")
        clean_row = rows[str(self.draft.id)]
        self.assertIsNone(clean_row["flagged_at"])
        self.assertEqual(clean_row["flag_reason"], "")

    def test_admin_list_filters_by_status_creator_and_search(self):
        self.client.force_authenticate(self.admin)

        response = self.client.get(
            "/api/v1/admin/courses/",
            {
                "status": CourseStatus.SUBMITTED,
                "creator": self.creator.id,
                "search": "Excel",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["data"]["results"]
        self.assertEqual([item["id"] for item in results], [str(self.submitted.id)])

    def test_admin_can_retrieve_complete_course(self):
        self.client.force_authenticate(self.admin)

        response = self.client.get(f"/api/v1/admin/courses/{self.draft.id}/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], str(self.draft.id))
        self.assertIn("modules", response.data)

    def test_reviewer_can_approve_content_through_admin_route(self):
        self.client.force_authenticate(self.reviewer)
        self.client.post(f"/api/v1/admin/courses/{self.submitted.id}/claim/")

        response = self.client.post(
            f"/api/v1/admin/courses/{self.submitted.id}/approve/",
            {"feedback": {"summary": "Content is complete."}},
            format="json",
        )

        # Content review now takes three seats, so one approval hands the
        # course to the next reviewer rather than straight to QA.
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.submitted.refresh_from_db()
        self.assertEqual(self.submitted.status, CourseStatus.SUBMITTED)
        self.assertEqual(self.submitted.review_stage, ReviewStage.SECOND_REVIEW)

    def test_creator_cannot_access_admin_courses(self):
        self.client.force_authenticate(self.creator)

        response = self.client.get("/api/v1/admin/courses/")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

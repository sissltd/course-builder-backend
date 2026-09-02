from rest_framework import status
from rest_framework.test import APITestCase

from api.courses.enums import CourseStatus
from api.courses.tests.factories import make_category, make_draft_course, make_user
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

        response = self.client.post(
            f"/api/v1/admin/courses/{self.submitted.id}/approve/",
            {"feedback": {"summary": "Content is complete."}},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.submitted.refresh_from_db()
        self.assertEqual(self.submitted.status, CourseStatus.QA_VERIFICATION)

    def test_creator_cannot_access_admin_courses(self):
        self.client.force_authenticate(self.creator)

        response = self.client.get("/api/v1/admin/courses/")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

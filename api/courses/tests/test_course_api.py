from rest_framework import status
from rest_framework.test import APITestCase

from api.courses.enums import CourseStatus
from api.courses.models import Course
from api.courses.services import course_service
from api.courses.tests.factories import build_compliant_course, make_category, make_draft_course, make_user
from api.users.enums import UserRole


class CourseApiTests(APITestCase):
    def setUp(self):
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.other_creator = make_user(role=UserRole.COURSE_CREATOR)
        self.admin = make_user(role=UserRole.ADMIN)
        self.category = make_category()

    def test_creator_can_create_draft_course(self):
        self.client.force_authenticate(self.creator)

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
        self.assertEqual(Course.objects.filter(creator=self.creator).count(), 1)

    def test_non_creator_role_forbidden_to_create(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            "/api/v1/courses/",
            {"category": str(self.category.id), "title": "X", "description": "d", "terms_accepted": True},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_creator_lists_only_own_courses(self):
        make_draft_course(creator=self.creator, category=self.category)
        make_draft_course(creator=self.other_creator, category=self.category)
        self.client.force_authenticate(self.creator)

        response = self.client.get("/api/v1/courses/")
        results = response.data["data"]["results"]
        self.assertEqual(len(results), 1)

    def test_creator_cannot_retrieve_others_course(self):
        course = make_draft_course(creator=self.other_creator, category=self.category)
        self.client.force_authenticate(self.creator)

        response = self.client.get(f"/api/v1/courses/{course.id}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_owner_can_update_draft(self):
        course = make_draft_course(creator=self.creator, category=self.category)
        self.client.force_authenticate(self.creator)

        response = self.client.patch(
            f"/api/v1/courses/{course.id}/", {"title": "New Title"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        course.refresh_from_db()
        self.assertEqual(course.title, "New Title")

    def test_cannot_update_once_submitted(self):
        course = build_compliant_course(creator=self.creator, category=self.category)
        course_service.submit_course(course=course, actor=self.creator)
        self.client.force_authenticate(self.creator)

        response = self.client.patch(
            f"/api/v1/courses/{course.id}/", {"title": "New Title"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_owner_can_delete_draft(self):
        course = make_draft_course(creator=self.creator, category=self.category)
        self.client.force_authenticate(self.creator)

        response = self.client.delete(f"/api/v1/courses/{course.id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_submit_happy_path(self):
        course = build_compliant_course(creator=self.creator, category=self.category)
        self.client.force_authenticate(self.creator)

        response = self.client.post(f"/api/v1/courses/{course.id}/submit/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        course.refresh_from_db()
        self.assertEqual(course.status, CourseStatus.SUBMITTED)

    def test_submit_fails_when_standards_not_met(self):
        course = make_draft_course(creator=self.creator, category=self.category)
        self.client.force_authenticate(self.creator)

        response = self.client.post(f"/api/v1/courses/{course.id}/submit/")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_publish_is_admin_only(self):
        course = build_compliant_course(creator=self.creator, category=self.category)
        course.status = CourseStatus.APPROVED
        course.save()
        self.client.force_authenticate(self.creator)

        response = self.client.post(f"/api/v1/courses/{course.id}/publish/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_publish_approved_course(self):
        course = build_compliant_course(creator=self.creator, category=self.category)
        course.status = CourseStatus.APPROVED
        course.save()
        self.client.force_authenticate(self.admin)

        response = self.client.post(f"/api/v1/courses/{course.id}/publish/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        course.refresh_from_db()
        self.assertEqual(course.status, CourseStatus.PUBLISHED)

    def test_publish_wrong_source_status(self):
        course = build_compliant_course(creator=self.creator, category=self.category)  # still Draft
        self.client.force_authenticate(self.admin)

        response = self.client.post(f"/api/v1/courses/{course.id}/publish/")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

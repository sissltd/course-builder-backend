from rest_framework import status
from rest_framework.test import APITestCase

from api.courses.enums import CourseStatus
from api.courses.models import Course
from api.courses.services import course_service
from api.courses.tests.factories import (
    build_compliant_course,
    make_category,
    make_draft_course,
    make_topic,
    make_user,
)
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

    def test_creator_can_create_with_topic_and_course_information_fields(self):
        topic = make_topic(category=self.category)
        self.client.force_authenticate(self.creator)

        response = self.client.post(
            "/api/v1/courses/",
            {
                "category": str(self.category.id),
                "topic": str(topic.id),
                "title": "My Course",
                "description": "d" * 20,
                "difficulty_level": "BEGINNER",
                "learning_objectives": ["Objective 1", "Objective 2"],
                "tags": ["Python", "Backend"],
                "thumbnail_url": "https://example.com/thumb.jpg",
                "duration_hours": 1,
                "duration_minutes": 30,
                "duration_seconds": 0,
                "terms_accepted": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        course = Course.objects.get(creator=self.creator)
        self.assertEqual(course.topic_id, topic.id)
        self.assertEqual(course.difficulty_level, "BEGINNER")
        self.assertEqual(course.learning_objectives, ["Objective 1", "Objective 2"])
        self.assertEqual(course.tags, ["Python", "Backend"])
        self.assertEqual(course.thumbnail_url, "https://example.com/thumb.jpg")
        self.assertEqual(course.planned_duration_seconds, 90 * 60)

    def test_create_with_mismatched_topic_and_category_rejected(self):
        other_category = make_category()
        topic = make_topic(category=other_category)
        self.client.force_authenticate(self.creator)

        response = self.client.post(
            "/api/v1/courses/",
            {
                "category": str(self.category.id),
                "topic": str(topic.id),
                "title": "My Course",
                "description": "d" * 20,
                "terms_accepted": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_with_non_string_tags_rejected(self):
        self.client.force_authenticate(self.creator)

        response = self.client.post(
            "/api/v1/courses/",
            {
                "category": str(self.category.id),
                "title": "My Course",
                "description": "d" * 20,
                "tags": ["ok", ""],
                "terms_accepted": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_partial_update_without_duration_leaves_it_unchanged(self):
        course = make_draft_course(
            creator=self.creator, category=self.category, planned_duration_seconds=120
        )
        self.client.force_authenticate(self.creator)

        response = self.client.patch(
            f"/api/v1/courses/{course.id}/", {"title": "New Title"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        course.refresh_from_db()
        self.assertEqual(course.planned_duration_seconds, 120)

    def test_partial_update_with_duration_recombines_it(self):
        course = make_draft_course(
            creator=self.creator, category=self.category, planned_duration_seconds=120
        )
        self.client.force_authenticate(self.creator)

        response = self.client.patch(
            f"/api/v1/courses/{course.id}/",
            {"duration_hours": 2, "duration_minutes": 0, "duration_seconds": 0},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        course.refresh_from_db()
        self.assertEqual(course.planned_duration_seconds, 2 * 3600)

    def test_non_creator_role_forbidden_to_create(self):
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            "/api/v1/courses/",
            {
                "category": str(self.category.id),
                "title": "X",
                "description": "d",
                "terms_accepted": True,
            },
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
        course = build_compliant_course(
            creator=self.creator, category=self.category
        )  # still Draft
        self.client.force_authenticate(self.admin)

        response = self.client.post(f"/api/v1/courses/{course.id}/publish/")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

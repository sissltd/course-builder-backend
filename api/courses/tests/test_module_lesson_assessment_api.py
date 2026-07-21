from rest_framework import status
from rest_framework.test import APITestCase

from api.courses.enums import CourseStatus
from api.courses.models import Lesson, Module
from api.courses.tests.factories import (
    make_category,
    make_collaborator,
    make_draft_course,
    make_user,
)
from api.users.enums import UserRole


class ModuleLessonAssessmentApiTests(APITestCase):
    def setUp(self):
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.other_creator = make_user(role=UserRole.COURSE_CREATOR)
        self.category = make_category()
        self.course = make_draft_course(creator=self.creator, category=self.category)

    def test_owner_can_create_module(self):
        self.client.force_authenticate(self.creator)

        response = self.client.post(
            f"/api/v1/courses/{self.course.id}/modules/",
            {"title": "Module 1", "order": 1},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_non_owner_gets_404_creating_module(self):
        self.client.force_authenticate(self.other_creator)

        response = self.client.post(
            f"/api/v1/courses/{self.course.id}/modules/",
            {"title": "Module 1", "order": 1},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_collaborator_can_create_module(self):
        collaborator_user = make_user()
        make_collaborator(course=self.course, user=collaborator_user)
        self.client.force_authenticate(collaborator_user)

        response = self.client.post(
            f"/api/v1/courses/{self.course.id}/modules/",
            {"title": "Module 1", "order": 1},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_collaborator_can_create_lesson_under_module(self):
        module = Module.objects.create(course=self.course, title="M1", order=1)
        collaborator_user = make_user()
        make_collaborator(course=self.course, user=collaborator_user)
        self.client.force_authenticate(collaborator_user)

        response = self.client.post(
            f"/api/v1/courses/{self.course.id}/modules/{module.id}/lessons/",
            {
                "title": "L1",
                "order": 1,
                "script": "x",
                "learning_objectives": ["a", "b"],
                "duration_minutes": 10,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_non_collaborator_still_gets_404_creating_module(self):
        # Regression check: the get_courses_accessible_to refactor must not
        # widen access beyond creator + actual collaborators.
        self.client.force_authenticate(self.other_creator)

        response = self.client.post(
            f"/api/v1/courses/{self.course.id}/modules/",
            {"title": "Module 1", "order": 1},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_cannot_create_module_when_course_not_draft(self):
        self.course.status = CourseStatus.SUBMITTED
        self.course.save()
        self.client.force_authenticate(self.creator)

        response = self.client.post(
            f"/api/v1/courses/{self.course.id}/modules/",
            {"title": "Module 1", "order": 1},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_owner_can_create_lesson_under_module(self):
        module = Module.objects.create(course=self.course, title="M1", order=1)
        self.client.force_authenticate(self.creator)

        response = self.client.post(
            f"/api/v1/courses/{self.course.id}/modules/{module.id}/lessons/",
            {
                "title": "L1",
                "order": 1,
                "script": "x",
                "learning_objectives": ["a", "b"],
                "duration_minutes": 10,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_cannot_create_lesson_when_course_not_draft(self):
        module = Module.objects.create(course=self.course, title="M1", order=1)
        self.course.status = CourseStatus.SUBMITTED
        self.course.save()
        self.client.force_authenticate(self.creator)

        response = self.client.post(
            f"/api/v1/courses/{self.course.id}/modules/{module.id}/lessons/",
            {"title": "L1", "order": 1, "learning_objectives": ["a", "b"]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_lesson_assessment_upsert(self):
        module = Module.objects.create(course=self.course, title="M1", order=1)
        lesson = Lesson.objects.create(module=module, title="L1", order=1)
        self.client.force_authenticate(self.creator)
        url = f"/api/v1/courses/{self.course.id}/modules/{module.id}/lessons/{lesson.id}/assessment/"

        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        payload = {
            "title": "Quiz 1",
            "questions": [
                {"question": "Q1?", "options": ["A", "B"], "correct_index": 0}
            ],
        }
        response = self.client.put(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["title"], "Quiz 1")

        payload["title"] = "Quiz 1 Updated"
        response = self.client.put(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["title"], "Quiz 1 Updated")

    def test_assessment_invalid_question_shape_rejected(self):
        module = Module.objects.create(course=self.course, title="M1", order=1)
        lesson = Lesson.objects.create(module=module, title="L1", order=1)
        self.client.force_authenticate(self.creator)
        url = f"/api/v1/courses/{self.course.id}/modules/{module.id}/lessons/{lesson.id}/assessment/"

        payload = {
            "title": "Quiz",
            "questions": [
                {"question": "Q1?", "options": ["only one"], "correct_index": 0}
            ],
        }
        response = self.client.put(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_assessment_missing_questions_rejected(self):
        module = Module.objects.create(course=self.course, title="M1", order=1)
        lesson = Lesson.objects.create(module=module, title="L1", order=1)
        self.client.force_authenticate(self.creator)
        url = f"/api/v1/courses/{self.course.id}/modules/{module.id}/lessons/{lesson.id}/assessment/"

        response = self.client.put(url, {"title": "Quiz"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(
            any(error["field_name"] == "questions" for error in response.data["errors"])
        )

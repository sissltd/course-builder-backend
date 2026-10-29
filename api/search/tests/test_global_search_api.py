from rest_framework import status
from rest_framework.test import APITestCase

from api.courses.enums import CourseStatus
from api.courses.tests.factories import (
    make_category,
    make_draft_course,
    make_topic,
    make_user,
)
from api.users.enums import UserRole


class GlobalSearchApiTests(APITestCase):
    def setUp(self):
        self.admin = make_user(role=UserRole.ADMIN)
        self.reviewer = make_user(role=UserRole.CREATOR_REVIEWER)
        self.creator = make_user(
            role=UserRole.COURSE_CREATOR,
            email="ada.creator@example.com",
            first_name="Ada",
            last_name="Creator",
        )
        self.other_creator = make_user(
            role=UserRole.COURSE_CREATOR,
            email="ben.creator@example.com",
            first_name="Ben",
            last_name="Creator",
        )
        self.category = make_category(name="Python Engineering")
        self.topic = make_topic(category=self.category, name="Python APIs")
        self.draft_course = make_draft_course(
            creator=self.creator,
            category=self.category,
            topic=self.topic,
            title="Python Draft Course",
        )
        self.submitted_course = make_draft_course(
            creator=self.other_creator,
            category=self.category,
            topic=self.topic,
            title="Python Review Course",
            status=CourseStatus.SUBMITTED,
        )

    def test_requires_authentication(self):
        response = self.client.get("/api/v1/global-search/", {"q": "python"})

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_requires_two_character_query(self):
        self.client.force_authenticate(self.admin)

        response = self.client.get("/api/v1/global-search/", {"q": "p"})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_admin_searches_users_and_all_courses(self):
        self.client.force_authenticate(self.admin)

        response = self.client.get("/api/v1/global-search/", {"q": "python"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["query"], "python")
        course_ids = {
            item["id"] for item in response.data["results"]["courses"]["results"]
        }
        self.assertIn(str(self.draft_course.id), course_ids)
        self.assertIn(str(self.submitted_course.id), course_ids)
        course_api_path = response.data["results"]["courses"]["results"][0][
            "api_path"
        ]
        self.assertEqual(
            course_api_path.split("/")[3],
            "admin",
        )

        user_response = self.client.get("/api/v1/global-search/", {"q": "ada"})
        self.assertEqual(user_response.status_code, status.HTTP_200_OK)
        self.assertIn("users", user_response.data["results"])
        self.assertEqual(
            user_response.data["results"]["users"]["results"][0]["id"],
            str(self.creator.id),
        )

    def test_reviewer_searches_only_reviewable_courses_and_no_users(self):
        self.client.force_authenticate(self.reviewer)

        response = self.client.get("/api/v1/global-search/", {"q": "python"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        course_ids = {
            item["id"] for item in response.data["results"]["courses"]["results"]
        }
        self.assertNotIn(str(self.draft_course.id), course_ids)
        self.assertIn(str(self.submitted_course.id), course_ids)
        self.assertNotIn("users", response.data["results"])
        course_api_path = response.data["results"]["courses"]["results"][0][
            "api_path"
        ]
        self.assertEqual(
            course_api_path.split("/")[3],
            "review-queue",
        )

    def test_creator_searches_only_own_courses(self):
        self.client.force_authenticate(self.creator)

        response = self.client.get("/api/v1/global-search/", {"q": "python"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        course_ids = {
            item["id"] for item in response.data["results"]["courses"]["results"]
        }
        self.assertIn(str(self.draft_course.id), course_ids)
        self.assertNotIn(str(self.submitted_course.id), course_ids)
        self.assertNotIn("users", response.data["results"])

    def test_limit_is_applied_per_bucket(self):
        make_draft_course(
            creator=self.creator,
            category=self.category,
            topic=self.topic,
            title="Python Second Course",
        )
        self.client.force_authenticate(self.creator)

        response = self.client.get(
            "/api/v1/global-search/",
            {"q": "python", "limit": 1},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["limit"], 1)
        self.assertEqual(response.data["results"]["courses"]["count"], 1)

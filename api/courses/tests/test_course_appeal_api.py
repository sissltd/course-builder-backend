from rest_framework import status
from rest_framework.test import APITestCase

from api.courses.enums import AppealStatus, CourseStatus
from api.courses.models import CourseAppeal
from api.courses.tests.factories import (
    make_course_appeal,
    make_rejected_course,
    make_user,
)
from api.users.enums import UserRole


class CourseAppealApiTests(APITestCase):
    def setUp(self):
        self.admin = make_user(role=UserRole.ADMIN)
        self.reviewer = make_user(role=UserRole.CREATOR_REVIEWER)
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.other_creator = make_user(role=UserRole.COURSE_CREATOR)

    def test_create_requires_authentication(self):
        course = make_rejected_course(creator=self.creator)

        response = self.client.post(
            "/api/v1/course-appeals/",
            {
                "course": str(course.id),
                "title": "Unfair rejection",
                "email": "creator@example.com",
                "description": "Please reconsider.",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_creator_can_submit_appeal_on_own_rejected_course(self):
        course = make_rejected_course(creator=self.creator)
        self.client.force_authenticate(self.creator)

        response = self.client.post(
            "/api/v1/course-appeals/",
            {
                "course": str(course.id),
                "title": "Unfair rejection",
                "email": "creator@example.com",
                "web_link": "https://example.com/portfolio",
                "description": "Please reconsider.",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], AppealStatus.PENDING)
        self.assertTrue(
            CourseAppeal.objects.filter(
                course=course, submitted_by=self.creator
            ).exists()
        )

    def test_cannot_appeal_a_course_that_was_never_rejected(self):
        from api.courses.tests.factories import make_draft_course

        course = make_draft_course(creator=self.creator)
        self.client.force_authenticate(self.creator)

        response = self.client.post(
            "/api/v1/course-appeals/",
            {
                "course": str(course.id),
                "title": "Unfair rejection",
                "email": "creator@example.com",
                "description": "Please reconsider.",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_creator_cannot_appeal_someone_elses_course(self):
        course = make_rejected_course(creator=self.other_creator)
        self.client.force_authenticate(self.creator)

        response = self.client.post(
            "/api/v1/course-appeals/",
            {
                "course": str(course.id),
                "title": "Unfair rejection",
                "email": "creator@example.com",
                "description": "Please reconsider.",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_creator_lists_only_own_appeals(self):
        make_course_appeal(
            course=make_rejected_course(creator=self.creator), submitted_by=self.creator
        )
        make_course_appeal(
            course=make_rejected_course(creator=self.other_creator),
            submitted_by=self.other_creator,
        )
        self.client.force_authenticate(self.creator)

        response = self.client.get("/api/v1/course-appeals/")
        self.assertEqual(len(response.data["data"]["results"]), 1)

    def test_admin_lists_all_appeals(self):
        make_course_appeal(
            course=make_rejected_course(creator=self.creator), submitted_by=self.creator
        )
        make_course_appeal(
            course=make_rejected_course(creator=self.other_creator),
            submitted_by=self.other_creator,
        )
        self.client.force_authenticate(self.admin)

        response = self.client.get("/api/v1/course-appeals/")
        self.assertEqual(len(response.data["data"]["results"]), 2)

    def test_creator_cannot_approve(self):
        appeal = make_course_appeal(
            course=make_rejected_course(creator=self.creator), submitted_by=self.creator
        )
        self.client.force_authenticate(self.creator)

        response = self.client.post(f"/api/v1/course-appeals/{appeal.id}/approve/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_creator_reviewer_cannot_approve(self):
        appeal = make_course_appeal(
            course=make_rejected_course(creator=self.creator), submitted_by=self.creator
        )
        self.client.force_authenticate(self.reviewer)

        response = self.client.post(f"/api/v1/course-appeals/{appeal.id}/approve/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_approve_resubmits_course(self):
        course = make_rejected_course(creator=self.creator)
        appeal = make_course_appeal(course=course, submitted_by=self.creator)
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            f"/api/v1/course-appeals/{appeal.id}/approve/",
            {"notes": "Confirmed up to date."},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], AppealStatus.APPROVED)
        course.refresh_from_db()
        self.assertEqual(course.status, CourseStatus.SUBMITTED)

    def test_admin_reject_leaves_course_untouched(self):
        course = make_rejected_course(creator=self.creator)
        appeal = make_course_appeal(course=course, submitted_by=self.creator)
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            f"/api/v1/course-appeals/{appeal.id}/reject/",
            {"notes": "Original rejection stands."},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], AppealStatus.REJECTED)
        course.refresh_from_db()
        self.assertEqual(course.status, CourseStatus.DRAFT)

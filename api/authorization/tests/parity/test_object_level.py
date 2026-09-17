"""Owner-scoped course gates for roles the course suite doesn't already cover.

`api/courses/tests/test_course_api.py` pins these for Admin. The Approver and
Writer reach the same gates through different role classes today, and will
reach them through different stored permissions afterwards, so they are
pinned here before conversion.
"""

from rest_framework import status
from rest_framework.test import APITestCase

from api.courses.tests.factories import (
    build_compliant_course,
    make_draft_course,
    make_user,
)
from api.users.enums import UserRole


class OwnerScopedCourseGateTests(APITestCase):
    def setUp(self):
        self.owner = make_user(role=UserRole.COURSE_CREATOR)

    def test_approver_edits_and_deletes_others_drafts(self):
        course = make_draft_course(creator=self.owner)
        self.client.force_authenticate(make_user(role=UserRole.STAFF_APPROVER))

        patch = self.client.patch(
            f"/api/v1/courses/{course.id}/", {"title": "X"}, format="json"
        )
        delete = self.client.delete(f"/api/v1/courses/{course.id}/")

        self.assertEqual(patch.status_code, status.HTTP_200_OK)
        self.assertEqual(delete.status_code, status.HTTP_204_NO_CONTENT)

    def test_approver_cannot_submit_others_course(self):
        course = build_compliant_course(creator=self.owner)
        self.client.force_authenticate(make_user(role=UserRole.STAFF_APPROVER))

        response = self.client.post(f"/api/v1/courses/{course.id}/submit/")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_writer_cannot_reach_another_authors_course(self):
        course = make_draft_course(creator=self.owner)
        self.client.force_authenticate(make_user(role=UserRole.STAFF_WRITER))

        for response in (
            self.client.get(f"/api/v1/courses/{course.id}/"),
            self.client.patch(
                f"/api/v1/courses/{course.id}/", {"title": "X"}, format="json"
            ),
            self.client.delete(f"/api/v1/courses/{course.id}/"),
        ):
            self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_reviewer_cannot_reach_the_authoring_route(self):
        course = make_draft_course(creator=self.owner)
        self.client.force_authenticate(make_user(role=UserRole.CREATOR_REVIEWER))

        response = self.client.patch(
            f"/api/v1/courses/{course.id}/", {"title": "X"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

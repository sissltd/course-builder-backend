from rest_framework import status
from rest_framework.test import APITestCase
from django.utils import timezone

from api.collaborators.enums import CollaboratorRole
from api.collaborators.models import CourseCollaborator
from api.collaborators.tests.factories import make_collaborator
from api.courses.tests.factories import make_draft_course, make_user
from api.users.enums import UserRole


class CollaboratorApiTests(APITestCase):
    def setUp(self):
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.course = make_draft_course(creator=self.creator)
        self.invitee = make_user(role=UserRole.COURSE_CREATOR)

    def test_list_visible_to_collaborator(self):
        collaborator_user = make_user()
        make_collaborator(course=self.course, user=collaborator_user)
        self.client.force_authenticate(collaborator_user)

        response = self.client.get(f"/api/v1/collaborators/?course_id={self.course.id}")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_list_response_uses_collaborators_screen_field_names(self):
        collaborator = make_collaborator(course=self.course, user=self.invitee)
        self.client.force_authenticate(self.creator)

        response = self.client.get(f"/api/v1/collaborators/?course_id={self.course.id}")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        row = response.data["data"]["results"][0]
        self.assertEqual(row["id"], str(collaborator.id))
        self.assertEqual(
            row["name"], f"{self.invitee.first_name} {self.invitee.last_name}"
        )
        self.assertEqual(row["email"], self.invitee.email)
        self.assertIn("date_added", row)
        self.assertEqual(row["role"], "COLLABORATOR")
        self.assertEqual(row["role_label"], "Collaborator")
        self.assertNotIn("user", row)
        self.assertNotIn("created_datetime", row)

    def test_list_filters_match_collaborators_screen_controls(self):
        matching = make_collaborator(course=self.course, user=self.invitee)
        admin_user = make_user(first_name="Ada", last_name="Admin")
        make_collaborator(
            course=self.course, user=admin_user, role=CollaboratorRole.ADMIN
        )
        self.client.force_authenticate(self.creator)

        response = self.client.get(
            f"/api/v1/collaborators/?course_id={self.course.id}"
            f"&search={self.invitee.email}&role=COLLABORATOR"
            f"&date_from={timezone.now().date()}&date_to={timezone.now().date()}"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["paginator"]["count"], 1)
        self.assertEqual(response.data["data"]["results"][0]["id"], str(matching.id))

    def test_list_requires_course_id(self):
        self.client.force_authenticate(self.creator)

        response = self.client.get("/api/v1/collaborators/")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_list_empty_for_non_collaborator(self):
        # Matches ModuleViewSet's own convention: list never calls get_object(),
        # so an inaccessible course's list is an empty 200, not a 404 (only
        # detail/create actions 404 for a course you can't access).
        outsider = make_user()
        self.client.force_authenticate(outsider)

        response = self.client.get(f"/api/v1/collaborators/?course_id={self.course.id}")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["data"]["results"]), 0)

    def test_creator_can_remove_collaborator(self):
        collaborator = make_collaborator(course=self.course, user=self.invitee)
        self.client.force_authenticate(self.creator)

        response = self.client.delete(f"/api/v1/collaborators/{collaborator.id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(CourseCollaborator.objects.filter(id=collaborator.id).exists())

    def test_plain_collaborator_cannot_remove(self):
        collaborator_user = make_user()
        acting_collaborator = make_collaborator(
            course=self.course, user=collaborator_user
        )
        other_collaborator = make_collaborator(course=self.course, user=self.invitee)
        self.client.force_authenticate(collaborator_user)

        response = self.client.delete(f"/api/v1/collaborators/{other_collaborator.id}/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(
            CourseCollaborator.objects.filter(id=acting_collaborator.id).exists()
        )

    def test_creator_can_change_role(self):
        collaborator = make_collaborator(course=self.course, user=self.invitee)
        self.client.force_authenticate(self.creator)

        response = self.client.patch(
            f"/api/v1/collaborators/{collaborator.id}/",
            {"role": "ADMIN"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        collaborator.refresh_from_db()
        self.assertEqual(collaborator.role, CollaboratorRole.ADMIN)

    def test_plain_collaborator_cannot_change_role(self):
        collaborator_user = make_user()
        make_collaborator(course=self.course, user=collaborator_user)
        other_collaborator = make_collaborator(course=self.course, user=self.invitee)
        self.client.force_authenticate(collaborator_user)

        response = self.client.patch(
            f"/api/v1/collaborators/{other_collaborator.id}/",
            {"role": "ADMIN"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

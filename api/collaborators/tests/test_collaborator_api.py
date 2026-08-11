from rest_framework import status
from rest_framework.test import APITestCase

from api.collaborators.enums import CollaboratorRole
from api.collaborators.models import CourseCollaborator
from api.collaborators.tests.factories import make_collaborator
from api.courses.tests.factories import make_draft_course, make_user
from api.notification.models import Notification
from api.users.enums import UserRole


class CollaboratorApiTests(APITestCase):
    def setUp(self):
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.course = make_draft_course(creator=self.creator)
        self.invitee = make_user(role=UserRole.COURSE_CREATOR)

    def test_creator_can_invite_existing_user(self):
        self.client.force_authenticate(self.creator)

        response = self.client.post(
            "/api/v1/collaborators/",
            {
                "course_id": self.course.id,
                "email": self.invitee.email,
                "role": "COLLABORATOR",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            CourseCollaborator.objects.filter(
                course=self.course, user=self.invitee
            ).exists()
        )
        self.assertTrue(
            Notification.objects.filter(
                receiver=self.invitee, title="Added as a collaborator"
            ).exists()
        )

    def test_invite_nonexistent_email_rejected(self):
        self.client.force_authenticate(self.creator)

        response = self.client.post(
            "/api/v1/collaborators/",
            {"course_id": self.course.id, "email": "nobody@example.com"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invite_own_creator_rejected(self):
        self.client.force_authenticate(self.creator)

        response = self.client.post(
            "/api/v1/collaborators/",
            {"course_id": self.course.id, "email": self.creator.email},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invite_same_user_twice_rejected(self):
        make_collaborator(course=self.course, user=self.invitee)
        self.client.force_authenticate(self.creator)

        response = self.client.post(
            "/api/v1/collaborators/",
            {"course_id": self.course.id, "email": self.invitee.email},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_plain_collaborator_cannot_invite(self):
        collaborator_user = make_user()
        make_collaborator(
            course=self.course,
            user=collaborator_user,
            role=CollaboratorRole.COLLABORATOR,
        )
        self.client.force_authenticate(collaborator_user)

        response = self.client.post(
            "/api/v1/collaborators/",
            {"course_id": self.course.id, "email": self.invitee.email},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_collaborator_can_invite(self):
        admin_user = make_user()
        make_collaborator(
            course=self.course, user=admin_user, role=CollaboratorRole.ADMIN
        )
        self.client.force_authenticate(admin_user)

        response = self.client.post(
            "/api/v1/collaborators/",
            {"course_id": self.course.id, "email": self.invitee.email},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_list_visible_to_collaborator(self):
        collaborator_user = make_user()
        make_collaborator(course=self.course, user=collaborator_user)
        self.client.force_authenticate(collaborator_user)

        response = self.client.get(f"/api/v1/collaborators/?course_id={self.course.id}")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

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

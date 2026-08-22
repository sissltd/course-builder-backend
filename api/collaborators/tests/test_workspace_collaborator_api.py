from rest_framework import status
from rest_framework.test import APITestCase

from api.collaborators.models import WorkspaceCollaborator
from api.courses.tests.factories import make_user
from api.users.enums import UserRole


class WorkspaceCollaboratorApiTests(APITestCase):
    def setUp(self):
        self.owner = make_user(role=UserRole.COURSE_CREATOR)
        self.other_creator = make_user(role=UserRole.COURSE_CREATOR)

    def test_owner_adds_collaborator_by_email(self):
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            "/api/v1/workspace-collaborators/",
            {"invited_email": "teammate@example.com", "role": "AUTHOR"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], "PENDING")
        row = WorkspaceCollaborator.objects.get(id=response.data["id"])
        self.assertEqual(row.owner_id, self.owner.id)
        self.assertIsNone(row.user_id)  # linked when they accept

    def test_duplicate_email_per_workspace_rejected(self):
        WorkspaceCollaborator.objects.create(
            owner=self.owner, invited_email="teammate@example.com"
        )
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            "/api/v1/workspace-collaborators/",
            {"invited_email": "teammate@example.com"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_same_email_ok_in_different_workspaces(self):
        WorkspaceCollaborator.objects.create(
            owner=self.other_creator, invited_email="shared@example.com"
        )
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            "/api/v1/workspace-collaborators/",
            {"invited_email": "shared@example.com"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_roster_is_scoped_to_owner(self):
        WorkspaceCollaborator.objects.create(
            owner=self.other_creator, invited_email="theirs@example.com"
        )
        mine = WorkspaceCollaborator.objects.create(
            owner=self.owner, invited_email="mine@example.com"
        )
        self.client.force_authenticate(self.owner)
        response = self.client.get("/api/v1/workspace-collaborators/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [r["id"] for r in response.data["data"]["results"]]
        self.assertEqual(ids, [str(mine.id)])

    def test_remove_is_soft(self):
        entry = WorkspaceCollaborator.objects.create(
            owner=self.owner, invited_email="gone@example.com"
        )
        self.client.force_authenticate(self.owner)
        response = self.client.delete(f"/api/v1/workspace-collaborators/{entry.id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        entry.refresh_from_db()
        self.assertEqual(entry.status, WorkspaceCollaborator.Status.REMOVED)
        self.assertIsNotNone(entry.removed_at)
        # Row survives for history.
        self.assertTrue(
            WorkspaceCollaborator.objects.filter(id=entry.id).exists()
        )

    def test_cannot_remove_twice(self):
        entry = WorkspaceCollaborator.objects.create(
            owner=self.owner,
            invited_email="gone@example.com",
            status=WorkspaceCollaborator.Status.REMOVED,
        )
        self.client.force_authenticate(self.owner)
        response = self.client.delete(f"/api/v1/workspace-collaborators/{entry.id}/")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_update_role(self):
        entry = WorkspaceCollaborator.objects.create(
            owner=self.owner, invited_email="promote@example.com"
        )
        self.client.force_authenticate(self.owner)
        response = self.client.patch(
            f"/api/v1/workspace-collaborators/{entry.id}/",
            {"role": "ADMIN"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        entry.refresh_from_db()
        self.assertEqual(entry.role, WorkspaceCollaborator.Role.ADMIN)

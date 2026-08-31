from datetime import timedelta

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from api.collaborators.models import CourseCollaborator, WorkspaceCollaborator
from api.collaborators.tests.factories import make_collaborator
from api.courses.tests.factories import make_category, make_draft_course, make_user
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

    def test_list_uses_figma_display_fields_and_linked_profile(self):
        member = make_user(
            first_name="Osaite", last_name="Emmanuel", sex="MALE", country="NG"
        )
        entry = WorkspaceCollaborator.objects.create(
            owner=self.owner,
            user=member,
            invited_email=member.email,
            role=WorkspaceCollaborator.Role.ADMIN,
            status=WorkspaceCollaborator.Status.ACTIVE,
        )
        self.client.force_authenticate(self.owner)

        response = self.client.get("/api/v1/workspace-collaborators/")

        row = response.data["data"]["results"][0]
        self.assertEqual(row["id"], str(entry.id))
        self.assertEqual(row["name"], "Osaite Emmanuel")
        self.assertEqual(row["email"], member.email)
        self.assertEqual(row["date_added"], row["created_datetime"])
        self.assertEqual(row["role_label"], "Admin")
        self.assertEqual(row["sex"], "MALE")
        self.assertEqual(row["country_of_origin"], "NG")

    def test_list_filters_by_search_role_and_date(self):
        member = make_user(first_name="Osaite", last_name="Emmanuel")
        matching = WorkspaceCollaborator.objects.create(
            owner=self.owner,
            user=member,
            invited_email=member.email,
            role=WorkspaceCollaborator.Role.ADMIN,
        )
        old = WorkspaceCollaborator.objects.create(
            owner=self.owner,
            invited_email="old@example.com",
            role=WorkspaceCollaborator.Role.COLLABORATOR,
        )
        WorkspaceCollaborator.objects.filter(id=old.id).update(
            created_datetime=timezone.now() - timedelta(days=30)
        )
        self.client.force_authenticate(self.owner)

        response = self.client.get(
            "/api/v1/workspace-collaborators/",
            {
                "search": "Osaite",
                "role": "ADMIN",
                "date_from": timezone.localdate().isoformat(),
            },
        )

        ids = [row["id"] for row in response.data["data"]["results"]]
        self.assertEqual(ids, [str(matching.id)])

    def test_list_filters_by_assigned_course_category(self):
        member = make_user()
        category = make_category()
        other_category = make_category()
        course = make_draft_course(creator=self.owner, category=category)
        other_course = make_draft_course(creator=self.owner, category=other_category)
        matching = WorkspaceCollaborator.objects.create(
            owner=self.owner, user=member, invited_email=member.email
        )
        other_member = make_user()
        WorkspaceCollaborator.objects.create(
            owner=self.owner, user=other_member, invited_email=other_member.email
        )
        make_collaborator(course=course, user=member)
        make_collaborator(course=other_course, user=other_member)
        self.client.force_authenticate(self.owner)

        response = self.client.get(
            "/api/v1/workspace-collaborators/", {"category": str(category.id)}
        )

        ids = [row["id"] for row in response.data["data"]["results"]]
        self.assertEqual(ids, [str(matching.id)])

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
        self.assertTrue(WorkspaceCollaborator.objects.filter(id=entry.id).exists())

    def test_remove_revokes_access_to_owners_courses_and_hides_roster_row(self):
        member = make_user()
        course = make_draft_course(creator=self.owner)
        collaboration = make_collaborator(course=course, user=member)
        entry = WorkspaceCollaborator.objects.create(
            owner=self.owner,
            user=member,
            invited_email=member.email,
            status=WorkspaceCollaborator.Status.ACTIVE,
        )
        self.client.force_authenticate(self.owner)

        response = self.client.delete(f"/api/v1/workspace-collaborators/{entry.id}/")

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(
            CourseCollaborator.objects.filter(id=collaboration.id).exists()
        )
        list_response = self.client.get("/api/v1/workspace-collaborators/")
        self.assertEqual(list_response.data["data"]["results"], [])

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

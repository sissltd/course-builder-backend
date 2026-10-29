from datetime import timedelta

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from api.collaborators.models import CourseCollaborator, WorkspaceCollaborator
from api.collaborators.tests.factories import make_collaborator
from api.courses.tests.factories import make_category, make_draft_course, make_user
from api.users.enums import UserRole


class WorkspaceCollaboratorAutoAcceptSignalTest(APITestCase):
    """Tests for the post_save signal that auto-accepts pending workspace
    invites when the invitee signs up with a matching email.

    Bug fix: Previously, when a Course Creator invited someone via the
    Collaborators menu and the invitee signed up and logged in, the
    WorkspaceCollaborator row stayed PENDING with user_id=NULL. The
    collaborator did not appear in the creator's Collaborators list because
    search and category filters require a linked user. This signal closes
    that gap by linking the user on creation.
    """

    def test_user_signup_auto_links_pending_workspace_invite(self):
        """When a user signs up with an email matching a pending invite,
        the signal links the user and sets status to ACTIVE."""
        owner = make_user(role=UserRole.COURSE_CREATOR)
        invite_email = "collab@example.com"

        # Course creator sends a workspace invite
        self.client.force_authenticate(owner)
        response = self.client.post(
            "/api/v1/workspace-collaborators/",
            {"invited_email": invite_email, "role": "COLLABORATOR"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], "PENDING")

        invite_id = response.data["id"]

        # The invitee signs up with the invited email
        invitee = make_user(email=invite_email, role=UserRole.COURSE_CREATOR)

        # Refresh and verify the signal linked them
        invite = WorkspaceCollaborator.objects.get(id=invite_id)
        self.assertEqual(invite.user_id, invitee.id)
        self.assertEqual(invite.status, WorkspaceCollaborator.Status.ACTIVE)

    def test_user_signup_appears_in_collaborators_list(self):
        """After signup, the auto-accepted collaborator appears in the
        owner's Collaborators list and can be found by search."""
        owner = make_user(role=UserRole.COURSE_CREATOR)
        invite_email = "jane.doe@example.com"

        # Send invite
        self.client.force_authenticate(owner)
        self.client.post(
            "/api/v1/workspace-collaborators/",
            {"invited_email": invite_email, "role": "COLLABORATOR"},
            format="json",
        )

        # Invitee signs up
        make_user(
            email=invite_email,
            first_name="Jane",
            last_name="Doe",
            role=UserRole.COURSE_CREATOR,
        )

        # Owner fetches collaborators list
        response = self.client.get("/api/v1/workspace-collaborators/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        results = response.data["data"]["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["email"], invite_email)
        self.assertEqual(results[0]["name"], "Jane Doe")
        self.assertEqual(results[0]["status"], "ACTIVE")

    def test_user_signup_finds_collaborator_by_search(self):
        """After signup, searching by name finds the collaborator."""
        owner = make_user(role=UserRole.COURSE_CREATOR)
        invite_email = "john.smith@example.com"

        # Send invite
        self.client.force_authenticate(owner)
        self.client.post(
            "/api/v1/workspace-collaborators/",
            {"invited_email": invite_email, "role": "COLLABORATOR"},
            format="json",
        )

        # Invitee signs up
        make_user(
            email=invite_email,
            first_name="John",
            last_name="Smith",
            role=UserRole.COURSE_CREATOR,
        )

        # Search by name
        response = self.client.get(
            "/api/v1/workspace-collaborators/",
            {"search": "John"},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["data"]["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["email"], invite_email)

    def test_user_signup_finds_collaborator_by_email_search(self):
        """After signup, searching by email finds the collaborator."""
        owner = make_user(role=UserRole.COURSE_CREATOR)
        invite_email = "john.smith@example.com"

        # Send invite
        self.client.force_authenticate(owner)
        self.client.post(
            "/api/v1/workspace-collaborators/",
            {"invited_email": invite_email, "role": "COLLABORATOR"},
            format="json",
        )

        # Invitee signs up
        make_user(
            email=invite_email,
            first_name="John",
            last_name="Smith",
            role=UserRole.COURSE_CREATOR,
        )

        # Search by email
        response = self.client.get(
            "/api/v1/workspace-collaborators/",
            {"search": "john.smith@example.com"},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["data"]["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["email"], invite_email)

    def test_resaving_existing_user_does_not_re_link(self):
        """Re-saving an existing user should not re-trigger the link."""
        owner = make_user(role=UserRole.COURSE_CREATOR)
        invite_email = "pending@example.com"

        # Send invite
        self.client.force_authenticate(owner)
        response = self.client.post(
            "/api/v1/workspace-collaborators/",
            {"invited_email": invite_email, "role": "COLLABORATOR"},
            format="json",
        )
        invite_id = response.data["id"]

        # User signs up (signal fires)
        user = make_user(email=invite_email, role=UserRole.COURSE_CREATOR)

        invite = WorkspaceCollaborator.objects.get(id=invite_id)
        self.assertEqual(invite.user_id, user.id)
        self.assertEqual(invite.status, WorkspaceCollaborator.Status.ACTIVE)

        # Re-save the user with updated info
        user.first_name = "Updated"
        user.save()

        # Status should still be ACTIVE, not re-processed
        invite.refresh_from_db()
        self.assertEqual(invite.user_id, user.id)
        self.assertEqual(invite.status, WorkspaceCollaborator.Status.ACTIVE)


class WorkspaceInviteAcceptApiTests(APITestCase):
    """Accepting a workspace invite with an account that already existed
    when the invite was sent. Signing up is not the only acceptance step -
    these actions are the equivalent, and acceptance is what makes the
    person show up on the owner's Collaborators list."""

    def setUp(self):
        self.owner = make_user(role=UserRole.COURSE_CREATOR)

    def _invite(self, email, **kwargs):
        defaults = {"owner": self.owner, "invited_email": email}
        defaults.update(kwargs)
        return WorkspaceCollaborator.objects.create(**defaults)

    def test_accept_links_existing_user_and_marks_active(self):
        invitee = make_user(role=UserRole.COURSE_CREATOR)
        invite = self._invite(invitee.email)
        self.client.force_authenticate(invitee)

        response = self.client.post(
            f"/api/v1/workspace-collaborators/{invite.id}/accept/"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        invite.refresh_from_db()
        self.assertEqual(invite.user_id, invitee.id)
        self.assertEqual(invite.status, WorkspaceCollaborator.Status.ACTIVE)

    def test_accepted_invite_appears_on_owners_global_collaborators_list(self):
        invitee = make_user(role=UserRole.COURSE_CREATOR)
        invite = self._invite(
            invitee.email, role=WorkspaceCollaborator.Role.AUTHOR
        )
        self.client.force_authenticate(invitee)
        self.client.post(f"/api/v1/workspace-collaborators/{invite.id}/accept/")
        self.client.force_authenticate(self.owner)

        response = self.client.get("/api/v1/collaborators/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows = response.data["data"]["results"]
        self.assertEqual([row["id"] for row in rows], [str(invite.id)])
        self.assertEqual(rows[0]["email"], invitee.email)
        self.assertEqual(rows[0]["role"], "AUTHOR")
        self.assertEqual(rows[0]["source"], "workspace")

    def test_accept_invite_addressed_to_other_email_404s(self):
        invite = self._invite("someone-else@example.com")
        self.client.force_authenticate(make_user(role=UserRole.COURSE_CREATOR))

        response = self.client.post(
            f"/api/v1/workspace-collaborators/{invite.id}/accept/"
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        invite.refresh_from_db()
        self.assertEqual(invite.status, WorkspaceCollaborator.Status.PENDING)

    def test_accept_already_accepted_invite_returns_400(self):
        invitee = make_user(role=UserRole.COURSE_CREATOR)
        invite = self._invite(
            invitee.email,
            status=WorkspaceCollaborator.Status.ACTIVE,
        )
        self.client.force_authenticate(invitee)

        response = self.client.post(
            f"/api/v1/workspace-collaborators/{invite.id}/accept/"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIsNone(invite.user_id)

    def test_accept_removed_invite_returns_400(self):
        invitee = make_user(role=UserRole.COURSE_CREATOR)
        invite = self._invite(
            invitee.email,
            status=WorkspaceCollaborator.Status.REMOVED,
        )
        self.client.force_authenticate(invitee)

        response = self.client.post(
            f"/api/v1/workspace-collaborators/{invite.id}/accept/"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        invite.refresh_from_db()
        self.assertIsNone(invite.user_id)

    def test_incoming_lists_only_pending_invites_for_caller_email(self):
        invitee = make_user(role=UserRole.COURSE_CREATOR)
        pending = self._invite(invitee.email)
        self._invite(
            invitee.email.replace("@example.com", "2@example.com"),
            owner=make_user(role=UserRole.COURSE_CREATOR),
        )  # addressed to someone else
        self._invite(
            "stranger@example.com",
            status=WorkspaceCollaborator.Status.ACTIVE,
        )  # accepted, not actionable
        self.client.force_authenticate(invitee)

        response = self.client.get("/api/v1/workspace-collaborators/incoming/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows = response.data["data"]["results"]
        self.assertEqual([row["id"] for row in rows], [str(pending.id)])
        self.assertEqual(rows[0]["email"], invitee.email)
        self.assertEqual(rows[0]["name"], invitee.email)

    def test_incoming_scoped_by_email_not_owner(self):
        """The invitee sees the invite across workspaces; the owner-only
        roster scoping doesn't apply to the invitee's inbox."""
        invitee = make_user(role=UserRole.COURSE_CREATOR)
        stranger_owner = make_user(role=UserRole.COURSE_CREATOR)
        invite = WorkspaceCollaborator.objects.create(
            owner=stranger_owner, invited_email=invitee.email
        )
        self.client.force_authenticate(invitee)

        response = self.client.get("/api/v1/workspace-collaborators/incoming/")

        rows = response.data["data"]["results"]
        self.assertEqual([row["id"] for row in rows], [str(invite.id)])


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

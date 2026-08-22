from datetime import timedelta

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from api.collaborators.enums import (
    CollaboratorInviteStatus,
    CollaboratorRole,
)
from api.collaborators.models import CollaboratorInvite, CourseCollaborator
from api.collaborators.services import invite_service
from api.courses.tests.factories import make_draft_course, make_user
from api.notification.models import Notification
from api.users.enums import UserRole


class InviteLifecycleApiTests(APITestCase):
    """End-to-end invite lifecycle over HTTP: create -> accept/decline,
    plus revoke and the guard rails around each transition."""

    def setUp(self):
        self.creator = make_user(role=UserRole.COURSE_CREATOR)
        self.course = make_draft_course(creator=self.creator)
        self.invitee = make_user(role=UserRole.COURSE_CREATOR)

    def _invite(self, email=None, **kwargs):
        defaults = {
            "course": self.course,
            "email": email or self.invitee.email,
            "role": CollaboratorRole.COLLABORATOR,
            "invited_by": self.creator,
            "expires_at": timezone.now()
            + timedelta(days=invite_service.INVITE_EXPIRY_DAYS),
        }
        defaults.update(kwargs)
        return CollaboratorInvite.objects.create(**defaults)

    # --- creating invites -------------------------------------------------

    def test_creator_creates_invite(self):
        self.client.force_authenticate(self.creator)
        response = self.client.post(
            "/api/v1/course-invites/",
            {
                "course_id": self.course.id,
                "email": self.invitee.email,
                "role": "COLLABORATOR",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], "PENDING")
        self.assertEqual(response.data["email"], self.invitee.email)
        self.assertNotIn("token", response.data)
        invite = CollaboratorInvite.objects.get(id=response.data["id"])
        self.assertEqual(invite.status, CollaboratorInviteStatus.PENDING)

    def test_invite_works_for_email_without_account(self):
        self.client.force_authenticate(self.creator)
        response = self.client.post(
            "/api/v1/course-invites/",
            {"course_id": self.course.id, "email": "not-signed-up@example.com"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(response.data["invitee"])

    def test_invite_own_creator_rejected(self):
        self.client.force_authenticate(self.creator)
        response = self.client.post(
            "/api/v1/course-invites/",
            {"course_id": self.course.id, "email": self.creator.email},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invite_existing_collaborator_rejected(self):
        CourseCollaborator.objects.create(course=self.course, user=self.invitee)
        self.client.force_authenticate(self.creator)
        response = self.client.post(
            "/api/v1/course-invites/",
            {"course_id": self.course.id, "email": self.invitee.email},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reinvite_supersedes_previous_pending_invite(self):
        stale = self._invite()
        self.client.force_authenticate(self.creator)
        response = self.client.post(
            "/api/v1/course-invites/",
            {"course_id": self.course.id, "email": self.invitee.email},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        stale.refresh_from_db()
        self.assertEqual(stale.status, CollaboratorInviteStatus.REVOKED)

    def test_plain_collaborator_cannot_invite(self):
        plain = make_user()
        CourseCollaborator.objects.create(course=self.course, user=plain)
        self.client.force_authenticate(plain)
        response = self.client.post(
            "/api/v1/course-invites/",
            {"course_id": self.course.id, "email": self.invitee.email},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # --- listing ----------------------------------------------------------

    def test_list_scoped_to_course_and_ordered_newest_first(self):
        old = self._invite(email="old@example.com")
        new = self._invite(email="new@example.com")
        self.client.force_authenticate(self.creator)
        response = self.client.get(f"/api/v1/course-invites/?course_id={self.course.id}")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [r["id"] for r in response.data["data"]["results"]]
        self.assertEqual(ids, [str(new.id), str(old.id)])

    def test_list_requires_course_id(self):
        self.client.force_authenticate(self.creator)
        response = self.client.get("/api/v1/course-invites/")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_incoming_lists_only_my_pending_invites(self):
        mine = self._invite(email=self.invitee.email)
        self._invite(email="someone-else@example.com")
        other_course_creator = make_user()
        other_course = make_draft_course(creator=other_course_creator)
        self._invite(
            course=other_course,
            invited_by=other_course_creator,
            email=self.invitee.email,
            status=CollaboratorInviteStatus.DECLINED,
        )

        self.client.force_authenticate(self.invitee)
        response = self.client.get("/api/v1/course-invites/incoming/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["data"]["results"]
        self.assertEqual([r["id"] for r in results], [str(mine.id)])

    # --- accepting --------------------------------------------------------

    def test_accept_flow_grants_access_and_notifies_inviter(self):
        invite = self._invite()
        self.client.force_authenticate(self.invitee)
        response = self.client.post(f"/api/v1/course-invites/{invite.id}/accept/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(CourseCollaborator.objects.filter(course=self.course, user=self.invitee).exists())
        invite.refresh_from_db()
        self.assertEqual(invite.status, CollaboratorInviteStatus.ACCEPTED)
        self.assertTrue(
            Notification.objects.filter(receiver=self.creator, title="Invite accepted").exists()
        )

    def test_accept_requires_authenticated_email_match(self):
        invite = self._invite(email="intended@example.com")
        impostor = make_user(email="impostor@example.com")
        self.client.force_authenticate(impostor)
        # A mismatched email 404s - invites not addressed to you don't
        # exist as far as the API is concerned.
        response = self.client.post(f"/api/v1/course-invites/{invite.id}/accept/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        invite.refresh_from_db()
        self.assertEqual(invite.status, CollaboratorInviteStatus.PENDING)

    def test_accept_expired_invite_rejected(self):
        invite = self._invite(expires_at=timezone.now() - timedelta(days=1))
        self.client.force_authenticate(self.invitee)
        response = self.client.post(f"/api/v1/course-invites/{invite.id}/accept/")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_accept_revoked_invite_rejected(self):
        invite = self._invite(status=CollaboratorInviteStatus.REVOKED)
        self.client.force_authenticate(self.invitee)
        response = self.client.post(f"/api/v1/course-invites/{invite.id}/accept/")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_accept_is_hidden_from_outsiders(self):
        invite = self._invite()
        outsider = make_user()
        self.client.force_authenticate(outsider)
        response = self.client.post(f"/api/v1/course-invites/{invite.id}/accept/")
        self.assertIn(
            response.status_code,
            {status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND},
        )

    # --- declining --------------------------------------------------------

    def test_decline_marks_declined_without_granting_access(self):
        invite = self._invite()
        self.client.force_authenticate(self.invitee)
        response = self.client.post(f"/api/v1/course-invites/{invite.id}/decline/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(CourseCollaborator.objects.filter(course=self.course, user=self.invitee).exists())
        invite.refresh_from_db()
        self.assertEqual(invite.status, CollaboratorInviteStatus.DECLINED)
        self.assertTrue(
            Notification.objects.filter(receiver=self.creator, title="Invite declined").exists()
        )

    # --- revoking ---------------------------------------------------------

    def test_creator_can_revoke_pending_invite(self):
        invite = self._invite()
        self.client.force_authenticate(self.creator)
        response = self.client.delete(f"/api/v1/course-invites/{invite.id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        invite.refresh_from_db()
        self.assertEqual(invite.status, CollaboratorInviteStatus.REVOKED)

    def test_cannot_revoke_accepted_invite(self):
        invite = self._invite(status=CollaboratorInviteStatus.ACCEPTED)
        self.client.force_authenticate(self.creator)
        response = self.client.delete(f"/api/v1/course-invites/{invite.id}/")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

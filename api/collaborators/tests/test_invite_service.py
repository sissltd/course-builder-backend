from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from api.collaborators.enums import (
    CollaboratorInviteStatus,
    CollaboratorRole,
)
from api.collaborators.models import CollaboratorInvite, CourseCollaborator
from api.collaborators.services import invite_service
from api.courses.models import Module
from api.courses.tests.factories import make_draft_course, make_user
from api.notification.models import Notification


def _make_invite(*, course=None, inviter=None, email="newbie@example.com", **kwargs):
    defaults = {
        "course": course or make_draft_course(),
        "email": email,
        "role": CollaboratorRole.COLLABORATOR,
        "invited_by": inviter,
        "expires_at": timezone.now() + timedelta(days=invite_service.INVITE_EXPIRY_DAYS),
    }
    defaults.update(kwargs)
    return CollaboratorInvite.objects.create(**defaults)


class CreateInviteTests(TestCase):
    def setUp(self):
        self.inviter = make_user()
        self.course = make_draft_course(creator=self.inviter)

    def test_creates_pending_invite_with_token_and_expiry(self):
        invite = invite_service.create_invite(
            course=self.course,
            inviter=self.inviter,
            email="New.Person@Example.com",
            role=CollaboratorRole.COLLABORATOR,
        )
        self.assertEqual(invite.status, CollaboratorInviteStatus.PENDING)
        self.assertEqual(invite.email, "new.person@example.com")
        self.assertTrue(invite.token)
        self.assertGreater(invite.expires_at, timezone.now())
        self.assertEqual(invite.invited_by_id, self.inviter.id)

    def test_notifies_invitee_when_account_exists(self):
        invitee = make_user(email="known@example.com")
        invite_service.create_invite(
            course=self.course,
            inviter=self.inviter,
            email=invitee.email,
            role=CollaboratorRole.COLLABORATOR,
        )
        self.assertTrue(
            Notification.objects.filter(
                receiver=invitee, title="Course collaboration invite"
            ).exists()
        )

    def test_skips_notification_when_no_account_yet(self):
        invite = invite_service.create_invite(
            course=self.course,
            inviter=self.inviter,
            email="ghost@example.com",
            role=CollaboratorRole.COLLABORATOR,
        )
        self.assertFalse(Notification.objects.filter(title="Course collaboration invite").exists())
        self.assertIsNone(invite.invitee_user)

    def test_raises_when_inviting_the_creator(self):
        with self.assertRaises(ValidationError):
            invite_service.create_invite(
                course=self.course,
                inviter=self.inviter,
                email=self.course.creator.email,
                role=CollaboratorRole.COLLABORATOR,
            )

    def test_raises_when_already_a_collaborator(self):
        existing_collaborator = make_user()
        CourseCollaborator.objects.create(
            course=self.course, user=existing_collaborator
        )
        with self.assertRaises(ValidationError):
            invite_service.create_invite(
                course=self.course,
                inviter=self.inviter,
                email=existing_collaborator.email,
                role=CollaboratorRole.COLLABORATOR,
            )

    def test_reinviting_supersedes_pending_invite(self):
        first = invite_service.create_invite(
            course=self.course,
            inviter=self.inviter,
            email="again@example.com",
            role=CollaboratorRole.COLLABORATOR,
        )
        second = invite_service.create_invite(
            course=self.course,
            inviter=self.inviter,
            email="AGAIN@example.com",
            role=CollaboratorRole.ADMIN,
        )
        first.refresh_from_db()
        self.assertEqual(first.status, CollaboratorInviteStatus.REVOKED)
        self.assertEqual(second.status, CollaboratorInviteStatus.PENDING)
        self.assertEqual(second.role, CollaboratorRole.ADMIN)

    def test_sets_assigned_modules(self):
        module = Module.objects.create(course=self.course, title="M1", order=0)
        invite = invite_service.create_invite(
            course=self.course,
            inviter=self.inviter,
            email="scoped@example.com",
            role=CollaboratorRole.COLLABORATOR,
            assigned_modules=[module],
        )
        self.assertEqual(list(invite.assigned_modules.all()), [module])

    def test_admin_invites_carry_no_module_scope(self):
        module = Module.objects.create(course=self.course, title="M1", order=0)
        invite = invite_service.create_invite(
            course=self.course,
            inviter=self.inviter,
            email="boss@example.com",
            role=CollaboratorRole.ADMIN,
            assigned_modules=[module],
        )
        self.assertEqual(invite.role, CollaboratorRole.ADMIN)
        self.assertEqual(invite.assigned_modules.count(), 0)

    def test_raises_when_assigned_module_belongs_to_another_course(self):
        foreign_module = Module.objects.create(
            course=make_draft_course(), title="Foreign", order=0
        )
        with self.assertRaises(ValidationError):
            invite_service.create_invite(
                course=self.course,
                inviter=self.inviter,
                email="oops@example.com",
                role=CollaboratorRole.COLLABORATOR,
                assigned_modules=[foreign_module],
            )


class AcceptInviteTests(TestCase):
    def setUp(self):
        self.inviter = make_user()
        self.course = make_draft_course(creator=self.inviter)
        self.module = Module.objects.create(course=self.course, title="M1", order=0)

    def _pending(self, email, **kwargs):
        return _make_invite(
            course=self.course, inviter=self.inviter, email=email, **kwargs
        )

    def test_accept_creates_collaborator_and_marks_accepted(self):
        invitee = make_user(email="joiner@example.com")
        invite = self._pending("joiner@example.com")
        invite.assigned_modules.set([self.module])

        collaborator = invite_service.accept_invite(invite=invite, user=invitee)

        self.assertEqual(collaborator.course_id, self.course.id)
        self.assertEqual(collaborator.user_id, invitee.id)
        self.assertEqual(collaborator.role, CollaboratorRole.COLLABORATOR)
        self.assertEqual(list(collaborator.assigned_modules.all()), [self.module])
        invite.refresh_from_db()
        self.assertEqual(invite.status, CollaboratorInviteStatus.ACCEPTED)
        self.assertIsNotNone(invite.responded_at)
        # The inviter hears about it.
        self.assertTrue(
            Notification.objects.filter(
                receiver=self.inviter, title="Invite accepted"
            ).exists()
        )

    def test_accept_admin_invite_grants_full_access_without_module_scope(self):
        invitee = make_user(email="admin@example.com")
        invite = self._pending(
            "admin@example.com", role=CollaboratorRole.ADMIN
        )
        collaborator = invite_service.accept_invite(invite=invite, user=invitee)
        self.assertEqual(collaborator.role, CollaboratorRole.ADMIN)

    def test_accept_requires_matching_email(self):
        invite = self._pending("intended@example.com")
        impostor = make_user(email="someone-else@example.com")
        with self.assertRaises(PermissionDenied):
            invite_service.accept_invite(invite=invite, user=impostor)

    def test_accept_blocked_after_decline(self):
        invitee = make_user(email="flip@example.com")
        invite = self._pending("flip@example.com")
        invite_service.decline_invite(invite=invite, user=invitee)
        with self.assertRaises(ValidationError):
            invite_service.accept_invite(invite=invite, user=invitee)

    def test_accept_blocked_once_expired(self):
        invitee = make_user(email="late@example.com")
        invite = self._pending(
            "late@example.com",
            expires_at=timezone.now() - timedelta(days=1),
        )
        self.assertTrue(invite_service.invite_is_expired(invite))
        with self.assertRaises(ValidationError):
            invite_service.accept_invite(invite=invite, user=invitee)

    def test_accept_blocked_if_already_collaborator(self):
        invitee = make_user(email="dupe@example.com")
        CourseCollaborator.objects.create(course=self.course, user=invitee)
        invite = self._pending("dupe@example.com")
        with self.assertRaises(ValidationError):
            invite_service.accept_invite(invite=invite, user=invitee)


class DeclineInviteTests(TestCase):
    def test_decline_marks_declined_and_notifies_inviter(self):
        inviter = make_user()
        course = make_draft_course(creator=inviter)
        invitee = make_user(email="no-thanks@example.com")
        invite = _make_invite(
            course=course, inviter=inviter, email="no-thanks@example.com"
        )

        result = invite_service.decline_invite(invite=invite, user=invitee)

        self.assertEqual(result.status, CollaboratorInviteStatus.DECLINED)
        self.assertIsNotNone(result.responded_at)
        self.assertFalse(
            CourseCollaborator.objects.filter(course=course, user=invitee).exists()
        )
        self.assertTrue(
            Notification.objects.filter(
                receiver=inviter, title="Invite declined"
            ).exists()
        )

    def test_decline_requires_matching_email(self):
        invite = _make_invite(email="theirs@example.com")
        stranger = make_user(email="mine@example.com")
        with self.assertRaises(PermissionDenied):
            invite_service.decline_invite(invite=invite, user=stranger)


class RevokeInviteTests(TestCase):
    def test_revoke_cancels_pending_invite(self):
        invite = _make_invite()

        result = invite_service.revoke_invite(invite=invite)

        self.assertEqual(result.status, CollaboratorInviteStatus.REVOKED)

    def test_revoke_blocked_on_accepted_invite(self):
        invite = _make_invite(status=CollaboratorInviteStatus.ACCEPTED)
        with self.assertRaises(ValidationError):
            invite_service.revoke_invite(invite=invite)


class InviteIsExpiredTests(TestCase):
    def test_false_for_fresh_pending_invite(self):
        invite = _make_invite()
        self.assertFalse(invite_service.invite_is_expired(invite))

    def test_true_for_pending_invite_past_expiry(self):
        invite = _make_invite(expires_at=timezone.now() - timedelta(minutes=5))
        self.assertTrue(invite_service.invite_is_expired(invite))

    def test_false_for_accepted_invite_even_if_past_expiry_date(self):
        invite = _make_invite(
            status=CollaboratorInviteStatus.ACCEPTED,
            expires_at=timezone.now() - timedelta(days=30),
        )
        self.assertFalse(invite_service.invite_is_expired(invite))

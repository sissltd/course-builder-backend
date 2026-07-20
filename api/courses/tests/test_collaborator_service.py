from django.test import TestCase
from rest_framework.exceptions import ValidationError

from api.courses.enums import CollaboratorRole
from api.courses.models import CourseCollaborator
from api.courses.services import collaborator_service
from api.courses.tests.factories import make_collaborator, make_draft_course, make_user
from api.notification.models import Notification


class InviteCollaboratorTests(TestCase):
    def test_creates_collaborator_and_notifies(self):
        creator = make_user()
        course = make_draft_course(creator=creator)
        invitee = make_user()

        collaborator = collaborator_service.invite_collaborator(
            course=course, inviter=creator, email=invitee.email, role=CollaboratorRole.COLLABORATOR
        )

        self.assertEqual(collaborator.user_id, invitee.id)
        self.assertEqual(collaborator.invited_by_id, creator.id)
        self.assertTrue(
            Notification.objects.filter(
                receiver=invitee, title="Added as a collaborator"
            ).exists()
        )

    def test_raises_when_email_has_no_account(self):
        creator = make_user()
        course = make_draft_course(creator=creator)

        with self.assertRaises(ValidationError):
            collaborator_service.invite_collaborator(
                course=course,
                inviter=creator,
                email="nobody@example.com",
                role=CollaboratorRole.COLLABORATOR,
            )

    def test_raises_when_inviting_the_creator(self):
        creator = make_user()
        course = make_draft_course(creator=creator)

        with self.assertRaises(ValidationError):
            collaborator_service.invite_collaborator(
                course=course,
                inviter=creator,
                email=creator.email,
                role=CollaboratorRole.COLLABORATOR,
            )

    def test_raises_on_duplicate_invite(self):
        creator = make_user()
        course = make_draft_course(creator=creator)
        invitee = make_user()
        make_collaborator(course=course, user=invitee)

        with self.assertRaises(ValidationError):
            collaborator_service.invite_collaborator(
                course=course,
                inviter=creator,
                email=invitee.email,
                role=CollaboratorRole.COLLABORATOR,
            )


class RemoveAndUpdateRoleTests(TestCase):
    def test_remove_collaborator_deletes_row(self):
        collaborator = make_collaborator()

        collaborator_service.remove_collaborator(collaborator=collaborator)

        self.assertFalse(CourseCollaborator.objects.filter(id=collaborator.id).exists())

    def test_update_collaborator_role(self):
        collaborator = make_collaborator(role=CollaboratorRole.COLLABORATOR)

        result = collaborator_service.update_collaborator_role(
            collaborator=collaborator, role=CollaboratorRole.ADMIN
        )

        self.assertEqual(result.role, CollaboratorRole.ADMIN)


class GetCoursesAccessibleToTests(TestCase):
    def test_includes_owned_and_collaborated_courses(self):
        creator = make_user()
        owned_course = make_draft_course(creator=creator)
        other_creator = make_user()
        collaborated_course = make_draft_course(creator=other_creator)
        make_collaborator(course=collaborated_course, user=creator)
        unrelated_course = make_draft_course()

        accessible_ids = set(
            collaborator_service.get_courses_accessible_to(creator).values_list(
                "id", flat=True
            )
        )

        self.assertIn(owned_course.id, accessible_ids)
        self.assertIn(collaborated_course.id, accessible_ids)
        self.assertNotIn(unrelated_course.id, accessible_ids)


class HasManageAccessTests(TestCase):
    def test_creator_has_manage_access(self):
        creator = make_user()
        course = make_draft_course(creator=creator)

        self.assertTrue(
            collaborator_service.has_manage_access(course=course, user=creator)
        )

    def test_admin_collaborator_has_manage_access(self):
        course = make_draft_course()
        admin_user = make_user()
        make_collaborator(course=course, user=admin_user, role=CollaboratorRole.ADMIN)

        self.assertTrue(
            collaborator_service.has_manage_access(course=course, user=admin_user)
        )

    def test_plain_collaborator_lacks_manage_access(self):
        course = make_draft_course()
        collaborator_user = make_user()
        make_collaborator(
            course=course, user=collaborator_user, role=CollaboratorRole.COLLABORATOR
        )

        self.assertFalse(
            collaborator_service.has_manage_access(course=course, user=collaborator_user)
        )

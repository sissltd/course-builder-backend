from django.test import TestCase
from rest_framework.exceptions import ValidationError

from api.collaborators.enums import CollaboratorRole
from api.collaborators.models import CourseCollaborator
from api.collaborators.services import collaborator_service
from api.collaborators.tests.factories import make_collaborator
from api.courses.models import Module
from api.courses.tests.factories import make_draft_course, make_user
from api.notification.models import Notification


class InviteCollaboratorTests(TestCase):
    def test_creates_collaborator_and_notifies(self):
        creator = make_user()
        course = make_draft_course(creator=creator)
        invitee = make_user()

        collaborator = collaborator_service.invite_collaborator(
            course=course,
            inviter=creator,
            email=invitee.email,
            role=CollaboratorRole.COLLABORATOR,
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

    def test_sets_assigned_modules_when_provided(self):
        creator = make_user()
        course = make_draft_course(creator=creator)
        module = Module.objects.create(course=course, title="M1", order=0)
        invitee = make_user()

        collaborator = collaborator_service.invite_collaborator(
            course=course,
            inviter=creator,
            email=invitee.email,
            role=CollaboratorRole.COLLABORATOR,
            assigned_modules=[module],
        )

        self.assertEqual(
            list(collaborator.assigned_modules.all().values_list("id", flat=True)),
            [module.id],
        )

    def test_raises_when_assigned_module_belongs_to_another_course(self):
        creator = make_user()
        course = make_draft_course(creator=creator)
        other_course = make_draft_course()
        foreign_module = Module.objects.create(
            course=other_course, title="Foreign", order=0
        )
        invitee = make_user()

        with self.assertRaises(ValidationError):
            collaborator_service.invite_collaborator(
                course=course,
                inviter=creator,
                email=invitee.email,
                role=CollaboratorRole.COLLABORATOR,
                assigned_modules=[foreign_module],
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

    def test_update_assigned_modules_without_touching_role(self):
        course = make_draft_course()
        module = Module.objects.create(course=course, title="M1", order=0)
        collaborator = make_collaborator(course=course, role=CollaboratorRole.ADMIN)

        result = collaborator_service.update_collaborator_role(
            collaborator=collaborator, assigned_modules=[module]
        )

        self.assertEqual(result.role, CollaboratorRole.ADMIN)
        self.assertEqual(
            list(result.assigned_modules.all().values_list("id", flat=True)),
            [module.id],
        )

    def test_raises_when_assigned_module_belongs_to_another_course(self):
        collaborator = make_collaborator()
        foreign_module = Module.objects.create(
            course=make_draft_course(), title="Foreign", order=0
        )

        with self.assertRaises(ValidationError):
            collaborator_service.update_collaborator_role(
                collaborator=collaborator, assigned_modules=[foreign_module]
            )


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


class GetModulesAccessibleToTests(TestCase):
    def test_creator_sees_all_modules(self):
        creator = make_user()
        course = make_draft_course(creator=creator)
        module_a = Module.objects.create(course=course, title="A", order=0)
        module_b = Module.objects.create(course=course, title="B", order=1)

        accessible_ids = set(
            collaborator_service.get_modules_accessible_to(
                user=creator, course_id=course.id
            ).values_list("id", flat=True)
        )

        self.assertEqual(accessible_ids, {module_a.id, module_b.id})

    def test_admin_collaborator_sees_all_modules(self):
        course = make_draft_course()
        module = Module.objects.create(course=course, title="A", order=0)
        admin_collaborator = make_user()
        make_collaborator(
            course=course, user=admin_collaborator, role=CollaboratorRole.ADMIN
        )

        accessible_ids = set(
            collaborator_service.get_modules_accessible_to(
                user=admin_collaborator, course_id=course.id
            ).values_list("id", flat=True)
        )

        self.assertEqual(accessible_ids, {module.id})

    def test_plain_collaborator_sees_only_assigned_modules(self):
        course = make_draft_course()
        assigned = Module.objects.create(course=course, title="Assigned", order=0)
        unassigned = Module.objects.create(course=course, title="Unassigned", order=1)
        collaborator_user = make_user()
        collaborator = make_collaborator(
            course=course, user=collaborator_user, role=CollaboratorRole.COLLABORATOR
        )
        collaborator.assigned_modules.set([assigned])

        accessible_ids = set(
            collaborator_service.get_modules_accessible_to(
                user=collaborator_user, course_id=course.id
            ).values_list("id", flat=True)
        )

        self.assertEqual(accessible_ids, {assigned.id})
        self.assertNotIn(unassigned.id, accessible_ids)

    def test_unrelated_user_sees_no_modules(self):
        course = make_draft_course()
        Module.objects.create(course=course, title="A", order=0)
        stranger = make_user()

        accessible = collaborator_service.get_modules_accessible_to(
            user=stranger, course_id=course.id
        )

        self.assertEqual(accessible.count(), 0)


class CanAccessModuleTests(TestCase):
    def test_true_for_assigned_module(self):
        course = make_draft_course()
        module = Module.objects.create(course=course, title="A", order=0)
        collaborator_user = make_user()
        collaborator = make_collaborator(
            course=course, user=collaborator_user, role=CollaboratorRole.COLLABORATOR
        )
        collaborator.assigned_modules.set([module])

        self.assertTrue(
            collaborator_service.can_access_module(user=collaborator_user, module=module)
        )

    def test_false_for_unassigned_module(self):
        course = make_draft_course()
        module = Module.objects.create(course=course, title="A", order=0)
        collaborator_user = make_user()
        make_collaborator(
            course=course, user=collaborator_user, role=CollaboratorRole.COLLABORATOR
        )

        self.assertFalse(
            collaborator_service.can_access_module(user=collaborator_user, module=module)
        )


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
            collaborator_service.has_manage_access(
                course=course, user=collaborator_user
            )
        )

"""Stored roles and permissions: seeding, the user link, and resolution."""

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework import status
from rest_framework.test import APITestCase

from api.authorization import codenames as c
from api.authorization.models import Role, RolePermission
from api.authorization.registry import ALL_CODENAMES, SYSTEM_ROLE_DEFAULT_GRANTS
from api.authorization.services import permission_service
from api.courses.tests.factories import make_user
from api.users.enums import UserRole
from api.users.models import User


class SystemRoleSeedDriftTests(TestCase):
    """The seed migration must grant exactly what the registry says.

    Fails when a codename is registered, or its default holders change,
    without a data migration applying that change to existing databases.
    """

    def test_seeded_grants_match_registry_defaults(self):
        seeded = {
            role.system_key: set(role.grants.values_list("codename", flat=True))
            for role in Role.objects.filter(system_key__isnull=False)
        }

        self.assertEqual(
            seeded,
            {role: set(codes) for role, codes in SYSTEM_ROLE_DEFAULT_GRANTS.items()},
        )

    def test_every_registered_codename_has_a_holder(self):
        held = set().union(*SYSTEM_ROLE_DEFAULT_GRANTS.values())

        self.assertEqual(held, set(ALL_CODENAMES))


class AccessRoleLinkTests(TestCase):
    def test_new_user_gets_the_system_role_for_their_role(self):
        user = make_user(role=UserRole.STAFF_VERIFIER)

        self.assertEqual(user.access_role.system_key, UserRole.STAFF_VERIFIER)

    def test_changing_role_moves_the_user_to_the_new_system_role(self):
        user = make_user(role=UserRole.STAFF_WRITER)
        user = User.objects.get(id=user.id)

        user.role = UserRole.STAFF_APPROVER
        user.save(update_fields=["role"])

        user.refresh_from_db()
        self.assertEqual(user.access_role.system_key, UserRole.STAFF_APPROVER)

    def test_an_explicit_access_role_set_with_the_role_change_is_kept(self):
        custom = Role.objects.create(
            name="Senior Writer", base_role=UserRole.STAFF_APPROVER
        )
        user = User.objects.get(id=make_user(role=UserRole.STAFF_WRITER).id)

        user.role = UserRole.STAFF_APPROVER
        user.access_role = custom
        user.save()

        user.refresh_from_db()
        self.assertEqual(user.access_role, custom)

    def test_a_missing_system_role_is_recreated_with_its_defaults(self):
        # TransactionTestCase flushes tables; users must still get a role.
        Role.objects.filter(system_key=UserRole.AI_REVIEWER).delete()

        user = make_user(role=UserRole.AI_REVIEWER)

        role = user.access_role
        self.assertEqual(role.system_key, UserRole.AI_REVIEWER)
        self.assertEqual(
            set(role.grants.values_list("codename", flat=True)),
            set(SYSTEM_ROLE_DEFAULT_GRANTS[UserRole.AI_REVIEWER]),
        )


class PermissionResolutionTests(TestCase):
    def tearDown(self):
        permission_service.close_request_memo()

    def _fresh(self, user):
        return User.objects.select_related("access_role").get(id=user.id)

    def test_default_grants_resolve(self):
        writer = self._fresh(make_user(role=UserRole.STAFF_WRITER))

        self.assertTrue(
            permission_service.user_has_permission(writer, c.COURSES_CREATE)
        )
        self.assertTrue(
            permission_service.user_has_permission(writer, c.ACHIEVEMENTS_MANAGE)
        )
        self.assertFalse(
            permission_service.user_has_permission(writer, c.PLATFORM_EDIT_SETTINGS)
        )

    def test_super_admin_and_superusers_hold_everything_without_a_query(self):
        for user in (
            self._fresh(make_user(role=UserRole.SUPER_ADMIN)),
            self._fresh(make_user(role=UserRole.COURSE_CREATOR, is_superuser=True)),
        ):
            with self.subTest(user=user.role), CaptureQueriesContext(connection) as ctx:
                self.assertEqual(
                    permission_service.get_permissions(user), ALL_CODENAMES
                )
            self.assertEqual(len(ctx.captured_queries), 0)

    def test_within_a_request_many_checks_cost_one_query(self):
        user = self._fresh(make_user(role=UserRole.ADMIN))
        permission_service.open_request_memo()

        with CaptureQueriesContext(connection) as ctx:
            for _ in range(10):
                permission_service.user_has_permission(user, c.AUDIT_VIEW)

        self.assertEqual(len(ctx.captured_queries), 1)

    def test_every_request_costs_the_same(self):
        user = self._fresh(make_user(role=UserRole.ADMIN))

        def one_request():
            permission_service.open_request_memo()
            with CaptureQueriesContext(connection) as ctx:
                permission_service.user_has_permission(user, c.AUDIT_VIEW)
            permission_service.close_request_memo()
            return len(ctx.captured_queries)

        self.assertEqual(one_request(), one_request())

    def test_a_grant_change_applies_to_the_next_request(self):
        role = Role.objects.create(name="Auditor", base_role=UserRole.STAFF_WRITER)
        RolePermission.objects.create(role=role, codename=c.AUDIT_VIEW)
        user = make_user(role=UserRole.STAFF_WRITER)
        User.objects.filter(id=user.id).update(access_role=role)
        user = self._fresh(user)
        permission_service.open_request_memo()
        self.assertTrue(permission_service.user_has_permission(user, c.AUDIT_VIEW))
        permission_service.close_request_memo()

        RolePermission.objects.filter(role=role).delete()

        permission_service.open_request_memo()
        self.assertFalse(permission_service.user_has_permission(user, c.AUDIT_VIEW))

    def test_forget_drops_the_memo_mid_request(self):
        role = Role.objects.create(name="Auditor", base_role=UserRole.STAFF_WRITER)
        RolePermission.objects.create(role=role, codename=c.AUDIT_VIEW)
        user = make_user(role=UserRole.STAFF_WRITER)
        User.objects.filter(id=user.id).update(access_role=role)
        user = self._fresh(user)
        permission_service.open_request_memo()
        self.assertTrue(permission_service.user_has_permission(user, c.AUDIT_VIEW))

        RolePermission.objects.filter(role=role).delete()
        permission_service.forget()

        self.assertFalse(permission_service.user_has_permission(user, c.AUDIT_VIEW))

    def test_unknown_stored_codenames_are_ignored_and_implications_expand(self):
        role = Role.objects.create(name="Staff lead", base_role=UserRole.STAFF_WRITER)
        RolePermission.objects.bulk_create(
            [
                RolePermission(role=role, codename="retired.permission"),
                RolePermission(role=role, codename=c.STAFF_FULL_ACCESS),
            ]
        )
        user = make_user(role=UserRole.STAFF_WRITER)
        User.objects.filter(id=user.id).update(access_role=role)

        permissions = permission_service.get_permissions(self._fresh(user))

        self.assertNotIn("retired.permission", permissions)
        self.assertTrue({c.STAFF_VIEW, c.STAFF_VIEW_DETAIL, c.STAFF_ADD} <= permissions)

    def test_require_permission_raises_for_a_missing_permission(self):
        from rest_framework.exceptions import PermissionDenied

        creator = self._fresh(make_user(role=UserRole.COURSE_CREATOR))

        with self.assertRaises(PermissionDenied):
            permission_service.require_permission(creator, c.AUDIT_VIEW)

    def test_users_with_permission_includes_implied_holders_and_super_admins(self):
        admin = make_user(role=UserRole.ADMIN)
        super_admin = make_user(role=UserRole.SUPER_ADMIN)
        make_user(role=UserRole.STAFF_WRITER)
        inactive_admin = make_user(role=UserRole.ADMIN, is_active=False)

        holders = set(permission_service.users_with_permission(c.AUDIT_VIEW))

        self.assertIn(admin, holders)
        self.assertIn(super_admin, holders)
        self.assertNotIn(inactive_admin, holders)
        self.assertEqual(
            {u.role for u in holders}, {UserRole.ADMIN, UserRole.SUPER_ADMIN}
        )


class MeAccessRoleTests(APITestCase):
    def test_me_lists_access_role_and_permissions(self):
        user = make_user(role=UserRole.STAFF_WRITER)
        self.client.force_authenticate(user)

        response = self.client.get("/api/v1/users/me/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["access_role"]["name"], "Writer")
        self.assertTrue(response.data["access_role"]["is_system"])
        self.assertEqual(
            response.data["access_role"]["base_role"], UserRole.STAFF_WRITER
        )
        self.assertEqual(
            response.data["permissions"],
            sorted(SYSTEM_ROLE_DEFAULT_GRANTS[UserRole.STAFF_WRITER]),
        )

"""The Roles & Permissions screen: role cards, chips, Add Role, change-role."""

from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
from rest_framework_simplejwt.tokens import RefreshToken

from api.authorization import codenames as c
from api.authorization.models import Role, RolePermission
from api.courses.tests.factories import make_user
from api.users.enums import UserRole
from api.users.models import User, UserActivityLog

ROLES_URL = "/api/v1/admin/roles/"
PERMISSIONS_URL = "/api/v1/admin/permissions/"


def role_url(role):
    return f"{ROLES_URL}{role.id}/"


def system_role(key):
    return Role.objects.get(system_key=key)


def make_role(*, name, base_role=UserRole.STAFF_WRITER, permissions=()):
    role = Role.objects.create(name=name, base_role=base_role)
    RolePermission.objects.bulk_create(
        [RolePermission(role=role, codename=code) for code in permissions]
    )
    return role


def make_member(role, **kwargs):
    user = make_user(role=role.base_role, **kwargs)
    User.objects.filter(id=user.id).update(access_role=role)
    return User.objects.select_related("access_role").get(id=user.id)


class RoleTestCase(APITestCase):
    def setUp(self):
        self.super_admin = make_user(role=UserRole.SUPER_ADMIN)

    def as_user(self, user):
        # Re-read so the permission memo from an earlier request never leaks
        # into the next one - the API always loads a fresh user per request.
        self.client.force_authenticate(
            User.objects.select_related("access_role").get(id=user.id)
        )


class RoleListTests(RoleTestCase):
    def test_built_in_roles_come_first_in_platform_order(self):
        make_role(name="Content Lead")
        self.as_user(self.super_admin)

        response = self.client.get(ROLES_URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows = response.data["data"]
        self.assertEqual([row["base_role"] for row in rows[:9]], list(UserRole.values))
        self.assertEqual(rows[9]["name"], "Content Lead")
        super_admin_card = rows[8]
        self.assertTrue(super_admin_card["is_locked"])
        self.assertFalse(super_admin_card["can_edit"])
        self.assertFalse(super_admin_card["is_deletable"])
        writer_card = rows[2]
        self.assertTrue(writer_card["is_system"])
        self.assertTrue(writer_card["can_edit"])
        self.assertIn(c.ACHIEVEMENTS_MANAGE, writer_card["permissions"])
        self.assertEqual(writer_card["base_role_label"], "Writer")

    def test_member_count_counts_active_members(self):
        role = make_role(name="Auditors", permissions=[c.AUDIT_VIEW])
        make_member(role)
        make_member(role, is_active=False)
        self.as_user(self.super_admin)

        rows = self.client.get(ROLES_URL).data["data"]

        self.assertEqual(
            next(r for r in rows if r["id"] == str(role.id))["member_count"], 1
        )

    def test_admin_can_view_but_not_edit(self):
        self.as_user(make_user(role=UserRole.ADMIN))

        rows = self.client.get(ROLES_URL).data["data"]

        self.assertTrue(all(row["can_edit"] is False for row in rows))

    def test_roles_without_roles_view_are_refused(self):
        for role in (
            UserRole.STAFF_WRITER,
            UserRole.STAFF_APPROVER,
            UserRole.COURSE_CREATOR,
        ):
            with self.subTest(role=role):
                self.as_user(make_user(role=role))
                self.assertEqual(
                    self.client.get(ROLES_URL).status_code, status.HTTP_403_FORBIDDEN
                )

    def test_unauthenticated_is_refused(self):
        self.assertEqual(
            self.client.get(ROLES_URL).status_code, status.HTTP_401_UNAUTHORIZED
        )

    def test_query_count_does_not_grow_with_roles(self):
        self.as_user(self.super_admin)

        def count():
            with CaptureQueriesContext(connection) as ctx:
                self.client.get(ROLES_URL)
            return len(ctx.captured_queries)

        make_role(name="Role 1", permissions=[c.AUDIT_VIEW])
        one = count()
        for n in range(2, 6):
            make_member(
                make_role(
                    name=f"Role {n}", permissions=[c.AUDIT_VIEW, c.DASHBOARD_VIEW]
                )
            )
        self.assertEqual(count(), one)

    def test_retrieve_and_deleted_role_404(self):
        role = make_role(name="Gone")
        self.as_user(self.super_admin)
        self.assertEqual(
            self.client.get(role_url(role)).status_code, status.HTTP_200_OK
        )

        Role.objects.filter(id=role.id).update(is_deleted=True)

        self.assertEqual(
            self.client.get(role_url(role)).status_code, status.HTTP_404_NOT_FOUND
        )


class PermissionCatalogueTests(RoleTestCase):
    def test_groups_and_what_the_caller_may_grant(self):
        self.as_user(self.super_admin)

        groups = self.client.get(PERMISSIONS_URL).data["data"]

        self.assertEqual(
            [g["key"] for g in groups[:6]],
            ["dashboard", "courses", "staff", "creators", "teams", "mie"],
        )
        courses = next(g for g in groups if g["key"] == "courses")
        approve = next(
            p for p in courses["permissions"] if p["codename"] == c.COURSES_APPROVE
        )
        self.assertEqual(approve["label"], "Approve Course")
        self.assertTrue(approve["grantable_by_you"])

    def test_a_viewer_may_grant_nothing(self):
        self.as_user(make_user(role=UserRole.ADMIN))

        groups = self.client.get(PERMISSIONS_URL).data["data"]

        self.assertFalse(
            any(p["grantable_by_you"] for g in groups for p in g["permissions"])
        )


class RoleCreateTests(RoleTestCase):
    def test_super_admin_creates_a_role(self):
        self.as_user(self.super_admin)

        response = self.client.post(
            ROLES_URL,
            {
                "name": "Content Lead",
                "base_role": UserRole.STAFF_WRITER,
                "permissions": [c.COURSES_CREATE, c.CATALOG_MANAGE_CATEGORIES],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        data = response.data["data"]
        self.assertEqual(
            data["permissions"], [c.CATALOG_MANAGE_CATEGORIES, c.COURSES_CREATE]
        )
        self.assertFalse(data["is_system"])
        self.assertTrue(
            UserActivityLog.objects.filter(
                user=self.super_admin, action="ROLE_CREATED"
            ).exists()
        )

    def test_a_role_can_be_based_on_creator_reviewer(self):
        self.as_user(self.super_admin)

        response = self.client.post(
            ROLES_URL,
            {
                "name": "Senior Reviewer",
                "base_role": UserRole.CREATOR_REVIEWER,
                "permissions": [c.COURSES_APPROVE, c.COURSES_REJECT],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["data"]["base_role"], UserRole.CREATOR_REVIEWER)

    def test_invalid_payloads_are_400(self):
        self.as_user(self.super_admin)
        for payload in (
            {"name": "X", "base_role": UserRole.COURSE_CREATOR, "permissions": []},
            {
                "name": "X",
                "base_role": UserRole.STAFF_WRITER,
                "permissions": ["made.up"],
            },
            {"base_role": UserRole.STAFF_WRITER, "permissions": []},
        ):
            with self.subTest(payload=payload):
                response = self.client.post(ROLES_URL, payload, format="json")
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_duplicate_name_is_409(self):
        make_role(name="Content Lead")
        self.as_user(self.super_admin)

        response = self.client.post(
            ROLES_URL,
            {
                "name": "content lead",
                "base_role": UserRole.STAFF_WRITER,
                "permissions": [],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_admin_without_roles_manage_is_refused(self):
        self.as_user(make_user(role=UserRole.ADMIN))

        response = self.client.post(
            ROLES_URL,
            {"name": "X", "base_role": UserRole.STAFF_WRITER, "permissions": []},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @override_settings(MFA_ENFORCED=True)
    def test_role_writes_need_a_challenged_mfa_session(self):
        user = User.objects.select_related("access_role").get(id=self.super_admin.id)
        payload = {"name": "X", "base_role": UserRole.STAFF_WRITER, "permissions": []}

        self.client.force_authenticate(
            user, token={"mfa_verified": True, "role": user.role}
        )
        refused = self.client.post(ROLES_URL, payload, format="json")
        self.client.force_authenticate(
            user,
            token={"mfa_verified": True, "mfa_challenged": True, "role": user.role},
        )
        allowed = self.client.post(ROLES_URL, payload, format="json")

        self.assertEqual(refused.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(allowed.status_code, status.HTTP_201_CREATED)


class RoleManagerEscalationTests(RoleTestCase):
    """A non-Super-Admin manager can never hand out more than they hold."""

    def setUp(self):
        super().setUp()
        self.manager_role = make_role(
            name="People Lead",
            base_role=UserRole.ADMIN,
            permissions=[c.ROLES_MANAGE, c.ROLES_VIEW, c.AUDIT_VIEW, c.COURSES_VIEW],
        )
        self.manager = make_member(self.manager_role)
        self.as_user(self.manager)

    def test_may_grant_what_they_hold(self):
        response = self.client.post(
            ROLES_URL,
            {
                "name": "Auditor",
                "base_role": UserRole.STAFF_WRITER,
                "permissions": [c.AUDIT_VIEW],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_may_not_grant_what_they_lack(self):
        response = self.client.post(
            ROLES_URL,
            {
                "name": "Settings",
                "base_role": UserRole.STAFF_WRITER,
                "permissions": [c.PLATFORM_EDIT_SETTINGS],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(Role.objects.filter(name="Settings").exists())

    def test_may_not_remove_what_they_lack(self):
        target = make_role(
            name="Finance", permissions=[c.CREATORS_VIEW_WALLET, c.AUDIT_VIEW]
        )

        response = self.client.patch(
            role_url(target), {"permissions": [c.AUDIT_VIEW]}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_untouched_permissions_they_lack_do_not_block_other_changes(self):
        target = make_role(name="Finance", permissions=[c.CREATORS_VIEW_WALLET])

        response = self.client.patch(
            role_url(target),
            {"permissions": [c.CREATORS_VIEW_WALLET, c.AUDIT_VIEW]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_may_not_edit_their_own_role(self):
        response = self.client.patch(
            role_url(self.manager_role), {"permissions": [c.ROLES_VIEW]}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_nobody_edits_the_super_admin_role(self):
        for user in (self.manager, self.super_admin):
            with self.subTest(user=user.role):
                self.as_user(user)
                response = self.client.patch(
                    role_url(system_role(UserRole.SUPER_ADMIN)),
                    {"permissions": []},
                    format="json",
                )
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class RoleUpdateTests(RoleTestCase):
    def test_removing_a_chip_takes_effect_on_the_next_request(self):
        role = make_role(name="Auditors", permissions=[c.AUDIT_VIEW])
        member = make_member(role)

        self.as_user(member)
        self.assertEqual(
            self.client.get("/api/v1/logs").status_code, status.HTTP_200_OK
        )

        self.as_user(self.super_admin)
        response = self.client.patch(role_url(role), {"permissions": []}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.as_user(member)
        self.assertEqual(
            self.client.get("/api/v1/logs").status_code, status.HTTP_403_FORBIDDEN
        )

    def test_a_single_chip_opens_its_endpoint_and_nothing_next_to_it(self):
        member = make_member(make_role(name="Auditors", permissions=[c.AUDIT_VIEW]))
        self.as_user(member)

        self.assertEqual(
            self.client.get("/api/v1/logs").status_code, status.HTTP_200_OK
        )
        self.assertEqual(
            self.client.get("/api/v1/admin/overview/").status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_built_in_roles_keep_their_name(self):
        self.as_user(self.super_admin)

        response = self.client.patch(
            role_url(system_role(UserRole.STAFF_WRITER)),
            {"name": "Author"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_public_roles_refuse_admin_level_permissions(self):
        self.as_user(self.super_admin)
        creators = system_role(UserRole.COURSE_CREATOR)
        current = list(creators.grants.values_list("codename", flat=True))

        refused = self.client.patch(
            role_url(creators),
            {"permissions": [*current, c.PLATFORM_EDIT_SETTINGS]},
            format="json",
        )
        allowed = self.client.patch(
            role_url(creators),
            {"permissions": [*current, c.COURSES_APPROVE]},
            format="json",
        )

        self.assertEqual(refused.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(allowed.status_code, status.HTTP_200_OK)

    def test_built_in_creator_reviewer_still_refuses_admin_level_permissions(self):
        # Creator Reviewer is staff now, but still reachable by public
        # signup, so its built-in role keeps the public-role guard.
        self.as_user(self.super_admin)
        reviewers = system_role(UserRole.CREATOR_REVIEWER)
        current = list(reviewers.grants.values_list("codename", flat=True))

        response = self.client.patch(
            role_url(reviewers),
            {"permissions": [*current, c.PLATFORM_EDIT_SETTINGS]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_empty_body_is_400(self):
        self.as_user(self.super_admin)

        response = self.client.patch(role_url(make_role(name="X")), {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class RoleDeleteTests(RoleTestCase):
    def setUp(self):
        super().setUp()
        self.as_user(self.super_admin)

    def test_built_in_roles_cannot_be_deleted(self):
        response = self.client.delete(role_url(system_role(UserRole.STAFF_WRITER)))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_role_with_members_needs_a_destination(self):
        role = make_role(name="Auditors")
        make_member(role)

        response = self.client.delete(role_url(role))

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertFalse(Role.objects.get(id=role.id).is_deleted)

    def test_members_can_only_move_to_the_same_base_role(self):
        role = make_role(name="Auditors")
        make_member(role)
        other = system_role(UserRole.STAFF_VERIFIER)

        response = self.client.delete(
            f"{role_url(role)}?reassign_to_role_id={other.id}"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_members_move_and_are_signed_out(self):
        role = make_role(name="Auditors")
        member = make_member(role)
        RefreshToken.for_user(member)
        writer = system_role(UserRole.STAFF_WRITER)

        response = self.client.delete(
            f"{role_url(role)}?reassign_to_role_id={writer.id}"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["members_moved"], 1)
        member.refresh_from_db()
        self.assertEqual(member.access_role, writer)
        self.assertTrue(BlacklistedToken.objects.filter(token__user=member).exists())
        self.assertTrue(Role.objects.get(id=role.id).is_deleted)

    def test_empty_role_is_deleted(self):
        role = make_role(name="Unused")

        response = self.client.delete(role_url(role))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["data"]["moved_to_role"])


class RoleMembersTests(RoleTestCase):
    def test_members_are_listed_and_searchable(self):
        role = make_role(name="Auditors")
        make_member(role, first_name="Ada")
        make_member(role, first_name="Bola")
        self.as_user(self.super_admin)

        everyone = self.client.get(f"{role_url(role)}members/")
        searched = self.client.get(f"{role_url(role)}members/", {"search": "ada"})

        self.assertEqual(everyone.data["data"]["paginator"]["count"], 2)
        self.assertEqual(searched.data["data"]["paginator"]["count"], 1)

    def test_admin_without_staff_view_is_refused(self):
        self.as_user(make_user(role=UserRole.ADMIN))

        response = self.client.get(f"{role_url(make_role(name='X'))}members/")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class ChangeStaffRoleTests(RoleTestCase):
    def url(self, user):
        return f"/api/v1/auth/staff/{user.id}/change-role/"

    def test_super_admin_moves_a_writer_to_a_custom_role(self):
        role = make_role(name="Content Lead", base_role=UserRole.STAFF_APPROVER)
        writer = make_user(role=UserRole.STAFF_WRITER)
        RefreshToken.for_user(writer)
        self.as_user(self.super_admin)

        response = self.client.post(
            self.url(writer), {"role_id": str(role.id)}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        writer.refresh_from_db()
        self.assertEqual(writer.role, UserRole.STAFF_APPROVER)
        self.assertEqual(writer.access_role, role)
        self.assertTrue(BlacklistedToken.objects.filter(token__user=writer).exists())
        self.assertTrue(
            UserActivityLog.objects.filter(
                user=writer, action="STAFF_ROLE_CHANGED"
            ).exists()
        )

    def test_refusals(self):
        role = make_role(name="Content Lead")
        self.as_user(self.super_admin)
        cases = (
            (make_user(role=UserRole.COURSE_CREATOR), status.HTTP_404_NOT_FOUND),
            (self.super_admin, status.HTTP_400_BAD_REQUEST),
        )
        for target, expected in cases:
            with self.subTest(target=target.role):
                response = self.client.post(
                    self.url(target), {"role_id": str(role.id)}, format="json"
                )
                self.assertEqual(response.status_code, expected)

    def test_creator_reviewer_moves_between_team_and_staff_roles(self):
        reviewer = make_user(role=UserRole.CREATOR_REVIEWER)
        self.as_user(self.super_admin)

        to_writer = self.client.post(
            self.url(reviewer),
            {"role_id": str(system_role(UserRole.STAFF_WRITER).id)},
            format="json",
        )
        reviewer.refresh_from_db()
        self.assertEqual(to_writer.status_code, status.HTTP_200_OK)
        self.assertEqual(reviewer.role, UserRole.STAFF_WRITER)

        back = self.client.post(
            self.url(reviewer),
            {"role_id": str(system_role(UserRole.CREATOR_REVIEWER).id)},
            format="json",
        )
        reviewer.refresh_from_db()
        self.assertEqual(back.status_code, status.HTTP_200_OK)
        self.assertEqual(reviewer.role, UserRole.CREATOR_REVIEWER)

    def test_admin_without_full_access_is_refused(self):
        self.as_user(make_user(role=UserRole.ADMIN))
        writer = make_user(role=UserRole.STAFF_WRITER)

        response = self.client.post(
            self.url(writer), {"role_id": str(make_role(name="X").id)}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

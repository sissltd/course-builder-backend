"""End-to-end coverage for the staff provisioning flows.

Covers the gates that matter most: the bootstrap endpoint must be unusable
without the environment secret, staff roles must be unreachable except through
a Super Admin's invitation, and the invite dialog's role choice must never be
able to mint a second Super Admin.
"""

from django.conf import settings
from django.core import mail
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from api.authentication.enums import TokenPurpose
from api.authentication.models import EmailVerificationToken
from api.authentication.tests.factories import make_user, make_verification_token
from api.users.enums import UserActivityActionEnums, UserRole
from api.users.models import User, UserActivityLog

BOOTSTRAP_URL = "/api/v1/auth/superadmin/bootstrap/"
STAFF_URL = "/api/v1/auth/staff/"
INVITE_URL = "/api/v1/auth/staff/invitations/"
ACCEPT_URL = "/api/v1/auth/staff/invitations/accept/"

BOOTSTRAP_TOKEN = "test-bootstrap-secret"

VALID_BOOTSTRAP_PAYLOAD = {
    "bootstrap_token": BOOTSTRAP_TOKEN,
    "email": "ops@example.com",
    "password": "StrongPass123!",
    "first_name": "Amara",
    "last_name": "Eze",
}


def revoke_url(staff):
    return f"/api/v1/auth/staff/{staff.id}/revoke/"


def reactivate_url(staff):
    return f"/api/v1/auth/staff/{staff.id}/reactivate/"


@override_settings(SUPERADMIN_BOOTSTRAP_TOKEN=BOOTSTRAP_TOKEN)
class SuperAdminBootstrapApiTests(APITestCase):
    def test_happy_path_creates_active_super_admin(self):
        response = self.client.post(
            BOOTSTRAP_URL, VALID_BOOTSTRAP_PAYLOAD, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["role"], UserRole.SUPER_ADMIN)
        self.assertTrue(response.data["is_active"])

        user = User.objects.get(email="ops@example.com")
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.check_password("StrongPass123!"))

    def test_bootstrapped_super_admin_can_log_in(self):
        self.client.post(BOOTSTRAP_URL, VALID_BOOTSTRAP_PAYLOAD, format="json")

        response = self.client.post(
            "/api/v1/auth/login/",
            {"email": "ops@example.com", "password": "StrongPass123!"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)

    def test_wrong_bootstrap_token_rejected(self):
        response = self.client.post(
            BOOTSTRAP_URL,
            {**VALID_BOOTSTRAP_PAYLOAD, "bootstrap_token": "wrong-secret"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(User.objects.filter(email="ops@example.com").exists())

    @override_settings(SUPERADMIN_BOOTSTRAP_TOKEN="")
    def test_unset_server_token_disables_endpoint(self):
        # The critical case: a deployment that forgot to set the variable must
        # not be claimable by whoever finds the URL first.
        response = self.client.post(
            BOOTSTRAP_URL,
            {**VALID_BOOTSTRAP_PAYLOAD, "bootstrap_token": "any-guess"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(User.objects.filter(role=UserRole.SUPER_ADMIN).exists())

    @override_settings(SUPERADMIN_BOOTSTRAP_TOKEN="")
    def test_blank_token_never_matches_unset_server_token(self):
        # Rejected at field validation (400) rather than the service's 403, but
        # what matters is that empty == empty never grants the seat.
        response = self.client.post(
            BOOTSTRAP_URL,
            {**VALID_BOOTSTRAP_PAYLOAD, "bootstrap_token": ""},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(User.objects.filter(role=UserRole.SUPER_ADMIN).exists())

    def test_second_bootstrap_rejected(self):
        self.client.post(BOOTSTRAP_URL, VALID_BOOTSTRAP_PAYLOAD, format="json")

        response = self.client.post(
            BOOTSTRAP_URL,
            {**VALID_BOOTSTRAP_PAYLOAD, "email": "second@example.com"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(User.objects.filter(role=UserRole.SUPER_ADMIN).count(), 1)

    def test_weak_password_rejected(self):
        response = self.client.post(
            BOOTSTRAP_URL,
            {**VALID_BOOTSTRAP_PAYLOAD, "password": "123"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_activity_logged(self):
        self.client.post(BOOTSTRAP_URL, VALID_BOOTSTRAP_PAYLOAD, format="json")

        self.assertTrue(
            UserActivityLog.objects.filter(
                action=UserActivityActionEnums.SUPERADMIN_BOOTSTRAPPED
            ).exists()
        )


class InviteStaffApiTests(APITestCase):
    def setUp(self):
        self.super_admin = make_user(
            email="super@example.com", role=UserRole.SUPER_ADMIN
        )
        self.payload = {
            "email": "newstaff@example.com",
            "first_name": "Tunde",
            "last_name": "Bakare",
            "role": UserRole.STAFF_WRITER,
        }

    def test_super_admin_can_invite(self):
        self.client.force_authenticate(self.super_admin)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(INVITE_URL, self.payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["staff"]["role"], UserRole.STAFF_WRITER)
        self.assertEqual(response.data["staff"]["role_label"], "Writer")
        self.assertEqual(response.data["staff"]["invitation_status"], "PENDING")

        invitee = User.objects.get(email="newstaff@example.com")
        self.assertFalse(invitee.has_usable_password())
        self.assertEqual(invitee.created_by, self.super_admin)

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(f"{settings.FRONTEND_URL}/accept-invitation", mail.outbox[0].body)

    def test_each_invitable_role_is_accepted(self):
        self.client.force_authenticate(self.super_admin)

        for index, role in enumerate(
            [UserRole.STAFF_WRITER, UserRole.STAFF_VERIFIER, UserRole.STAFF_APPROVER]
        ):
            with self.subTest(role=role):
                email = f"staff{index}@example.com"
                with self.captureOnCommitCallbacks(execute=True):
                    response = self.client.post(
                        INVITE_URL,
                        {**self.payload, "email": email, "role": role},
                        format="json",
                    )

                self.assertEqual(response.status_code, status.HTTP_201_CREATED)
                self.assertEqual(User.objects.get(email=email).role, role)

    def test_role_label_appears_in_invitation_email(self):
        self.client.force_authenticate(self.super_admin)

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                INVITE_URL,
                {**self.payload, "role": UserRole.STAFF_APPROVER},
                format="json",
            )

        self.assertIn("Approver", mail.outbox[0].body)

    def test_cannot_invite_a_second_super_admin(self):
        # The escalation this endpoint must never permit.
        self.client.force_authenticate(self.super_admin)

        response = self.client.post(
            INVITE_URL, {**self.payload, "role": UserRole.SUPER_ADMIN}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(User.objects.filter(role=UserRole.SUPER_ADMIN).count(), 1)

    def test_cannot_invite_into_a_public_role(self):
        self.client.force_authenticate(self.super_admin)

        response = self.client.post(
            INVITE_URL, {**self.payload, "role": UserRole.COURSE_CREATOR}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_role_is_required(self):
        self.client.force_authenticate(self.super_admin)
        payload = {k: v for k, v in self.payload.items() if k != "role"}

        response = self.client.post(INVITE_URL, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_approver_cannot_invite(self):
        # Only the Super Admin hands out staff seats.
        approver = make_user(email="approver@example.com", role=UserRole.STAFF_APPROVER)
        self.client.force_authenticate(approver)

        response = self.client.post(INVITE_URL, self.payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(User.objects.filter(email="newstaff@example.com").exists())

    def test_plain_admin_cannot_invite(self):
        admin = make_user(email="admin@example.com", role=UserRole.ADMIN)
        self.client.force_authenticate(admin)

        response = self.client.post(INVITE_URL, self.payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_course_creator_cannot_invite(self):
        creator = make_user(email="creator@example.com")
        self.client.force_authenticate(creator)

        response = self.client.post(INVITE_URL, self.payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_anonymous_cannot_invite(self):
        response = self.client.post(INVITE_URL, self.payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_inviting_existing_active_user_rejected(self):
        make_user(email="taken@example.com")
        self.client.force_authenticate(self.super_admin)

        response = self.client.post(
            INVITE_URL, {**self.payload, "email": "taken@example.com"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @override_settings(EMAIL_TOKEN_RESEND_COOLDOWN_SECONDS=0)
    def test_reinviting_pending_staff_reissues_token(self):
        self.client.force_authenticate(self.super_admin)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(INVITE_URL, self.payload, format="json")
        first_token = EmailVerificationToken.objects.get(
            purpose=TokenPurpose.STAFF_INVITATION, is_used=False
        )

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(INVITE_URL, self.payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(User.objects.filter(email="newstaff@example.com").count(), 1)
        first_token.refresh_from_db()
        self.assertTrue(
            first_token.is_used, "previous invitation should be invalidated"
        )
        self.assertEqual(len(mail.outbox), 2)

    @override_settings(EMAIL_TOKEN_RESEND_COOLDOWN_SECONDS=0)
    def test_reinviting_pending_staff_can_correct_the_role(self):
        self.client.force_authenticate(self.super_admin)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(INVITE_URL, self.payload, format="json")

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                INVITE_URL,
                {**self.payload, "role": UserRole.STAFF_VERIFIER},
                format="json",
            )

        invitee = User.objects.get(email="newstaff@example.com")
        self.assertEqual(invitee.role, UserRole.STAFF_VERIFIER)

    def test_reinvite_within_cooldown_rejected(self):
        self.client.force_authenticate(self.super_admin)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(INVITE_URL, self.payload, format="json")

        response = self.client.post(INVITE_URL, self.payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class AcceptStaffInvitationApiTests(APITestCase):
    def setUp(self):
        self.invitee = make_user(
            email="pending@example.com",
            role=UserRole.STAFF_WRITER,
            is_active=False,
            password=None,
        )
        self.token = make_verification_token(
            user=self.invitee,
            purpose=TokenPurpose.STAFF_INVITATION,
            raw_token="invite-token-abc",
        )
        self.payload = {
            "email": "pending@example.com",
            "token": "invite-token-abc",
            "password": "StrongPass123!",
        }

    def test_happy_path_activates_and_returns_tokens(self):
        response = self.client.post(ACCEPT_URL, self.payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
        self.assertTrue(response.data["user"]["is_active"])

        self.invitee.refresh_from_db()
        self.assertTrue(self.invitee.is_active)
        self.assertTrue(self.invitee.check_password("StrongPass123!"))
        self.assertEqual(self.invitee.role, UserRole.STAFF_WRITER)

    def test_accepted_staff_can_log_in(self):
        self.client.post(ACCEPT_URL, self.payload, format="json")

        response = self.client.post(
            "/api/v1/auth/login/",
            {"email": "pending@example.com", "password": "StrongPass123!"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_token_is_single_use(self):
        self.client.post(ACCEPT_URL, self.payload, format="json")

        response = self.client.post(ACCEPT_URL, self.payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_acceptance_cannot_change_the_invited_role(self):
        response = self.client.post(
            ACCEPT_URL,
            {**self.payload, "role": UserRole.SUPER_ADMIN},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.invitee.refresh_from_db()
        self.assertEqual(self.invitee.role, UserRole.STAFF_WRITER)

    def test_wrong_token_rejected(self):
        response = self.client.post(
            ACCEPT_URL, {**self.payload, "token": "not-the-token"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.invitee.refresh_from_db()
        self.assertFalse(self.invitee.is_active)

    def test_unknown_email_rejected_without_enumeration(self):
        response = self.client.post(
            ACCEPT_URL, {**self.payload, "email": "nobody@example.com"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_token_belonging_to_another_user_rejected(self):
        other = make_user(email="other@example.com", role=UserRole.STAFF_WRITER)

        response = self.client.post(
            ACCEPT_URL, {**self.payload, "email": other.email}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.invitee.refresh_from_db()
        self.assertFalse(self.invitee.is_active)

    def test_password_reset_token_cannot_accept_invitation(self):
        # Purpose separation: a token minted for one flow must not unlock another.
        make_verification_token(
            user=self.invitee,
            purpose=TokenPurpose.PASSWORD_RESET,
            raw_token="reset-token-xyz",
        )

        response = self.client.post(
            ACCEPT_URL, {**self.payload, "token": "reset-token-xyz"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_weak_password_rejected(self):
        response = self.client.post(
            ACCEPT_URL, {**self.payload, "password": "123"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.invitee.refresh_from_db()
        self.assertFalse(self.invitee.is_active)

    def test_activity_logged(self):
        self.client.post(ACCEPT_URL, self.payload, format="json")

        self.assertTrue(
            UserActivityLog.objects.filter(
                user=self.invitee,
                action=UserActivityActionEnums.STAFF_INVITATION_ACCEPTED,
            ).exists()
        )


class StaffListApiTests(APITestCase):
    def setUp(self):
        self.super_admin = make_user(
            email="super@example.com", role=UserRole.SUPER_ADMIN
        )
        self.active_staff = make_user(
            email="writer@example.com", role=UserRole.STAFF_WRITER
        )
        self.pending_staff = make_user(
            email="pending@example.com",
            role=UserRole.STAFF_VERIFIER,
            is_active=False,
            password=None,
        )
        # invite_staff() always issues a token alongside the pending row; without
        # one the account reads as an invitation that was already withdrawn.
        make_verification_token(
            user=self.pending_staff,
            purpose=TokenPurpose.STAFF_INVITATION,
            raw_token="listed-pending-token",
        )
        self.public_user = make_user(email="public@example.com")

    def test_super_admin_sees_staff_with_statuses(self):
        self.client.force_authenticate(self.super_admin)

        response = self.client.get(STAFF_URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        by_email = {row["email"]: row for row in response.data}
        self.assertEqual(by_email["writer@example.com"]["invitation_status"], "ACTIVE")
        self.assertEqual(
            by_email["pending@example.com"]["invitation_status"], "PENDING"
        )
        self.assertEqual(by_email["pending@example.com"]["role_label"], "Verifier")

    def test_public_users_are_not_listed_as_staff(self):
        self.client.force_authenticate(self.super_admin)

        response = self.client.get(STAFF_URL)

        emails = {row["email"] for row in response.data}
        self.assertNotIn("public@example.com", emails)

    def test_non_super_admin_cannot_list_staff(self):
        self.client.force_authenticate(self.active_staff)

        response = self.client.get(STAFF_URL)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_anonymous_cannot_list_staff(self):
        response = self.client.get(STAFF_URL)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class RevokeStaffApiTests(APITestCase):
    def setUp(self):
        self.super_admin = make_user(
            email="super@example.com", role=UserRole.SUPER_ADMIN
        )
        self.staff = make_user(email="writer@example.com", role=UserRole.STAFF_WRITER)
        self.client.force_authenticate(self.super_admin)

    def test_revoking_active_staff_deactivates_them(self):
        response = self.client.post(revoke_url(self.staff))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["staff"]["invitation_status"], "REVOKED")
        self.staff.refresh_from_db()
        self.assertFalse(self.staff.is_active)

    def test_revoked_staff_cannot_log_in(self):
        self.client.post(revoke_url(self.staff))

        self.client.force_authenticate(None)
        response = self.client.post(
            "/api/v1/auth/login/",
            {"email": "writer@example.com", "password": "testpass123"},
            format="json",
        )

        self.assertNotEqual(response.status_code, status.HTTP_200_OK)

    def test_revoking_pending_invite_burns_the_token(self):
        invitee = make_user(
            email="pending@example.com",
            role=UserRole.STAFF_WRITER,
            is_active=False,
            password=None,
        )
        make_verification_token(
            user=invitee,
            purpose=TokenPurpose.STAFF_INVITATION,
            raw_token="pending-token",
        )

        self.client.post(revoke_url(invitee))

        self.client.force_authenticate(None)
        response = self.client.post(
            ACCEPT_URL,
            {
                "email": "pending@example.com",
                "token": "pending-token",
                "password": "StrongPass123!",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_cannot_revoke_self(self):
        response = self.client.post(revoke_url(self.super_admin))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.super_admin.refresh_from_db()
        self.assertTrue(self.super_admin.is_active)

    # Note: there is no "revoke a *different* super admin" test because the
    # unique_super_admin constraint makes a second one unconstructible - the
    # only reachable super-admin target is the caller, covered above. The
    # service keeps a separate SUPER_ADMIN guard as defense in depth.

    def test_cannot_revoke_a_public_user(self):
        public_user = make_user(email="public@example.com")

        response = self.client.post(revoke_url(public_user))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        public_user.refresh_from_db()
        self.assertTrue(public_user.is_active)

    def test_revoking_twice_rejected(self):
        self.client.post(revoke_url(self.staff))

        response = self.client.post(revoke_url(self.staff))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_revoked_pending_invite_reports_revoked_not_pending(self):
        # Pending and revoked are both is_active=False; the Teams page offers
        # different actions for each, so the status must tell them apart.
        invitee = make_user(
            email="pending@example.com",
            role=UserRole.STAFF_WRITER,
            is_active=False,
            password=None,
        )
        make_verification_token(
            user=invitee,
            purpose=TokenPurpose.STAFF_INVITATION,
            raw_token="pending-token",
        )

        listed = self.client.get(STAFF_URL).data
        before = next(r for r in listed if r["email"] == "pending@example.com")
        self.assertEqual(before["invitation_status"], "PENDING")

        self.client.post(revoke_url(invitee))

        listed = self.client.get(STAFF_URL).data
        after = next(r for r in listed if r["email"] == "pending@example.com")
        self.assertEqual(after["invitation_status"], "REVOKED")

    def test_unknown_staff_returns_404(self):
        response = self.client.post(
            "/api/v1/auth/staff/00000000-0000-0000-0000-000000000000/revoke/"
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_non_super_admin_cannot_revoke(self):
        approver = make_user(email="approver@example.com", role=UserRole.STAFF_APPROVER)
        self.client.force_authenticate(approver)

        response = self.client.post(revoke_url(self.staff))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_activity_logged(self):
        self.client.post(revoke_url(self.staff))

        self.assertTrue(
            UserActivityLog.objects.filter(
                action=UserActivityActionEnums.STAFF_REVOKED
            ).exists()
        )


class ReactivateStaffApiTests(APITestCase):
    def setUp(self):
        self.super_admin = make_user(
            email="super@example.com", role=UserRole.SUPER_ADMIN
        )
        self.staff = make_user(email="writer@example.com", role=UserRole.STAFF_WRITER)
        self.client.force_authenticate(self.super_admin)

    def test_reactivating_revoked_staff_restores_access(self):
        self.client.post(revoke_url(self.staff))

        response = self.client.post(reactivate_url(self.staff))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["staff"]["invitation_status"], "ACTIVE")
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.is_active)
        self.assertEqual(self.staff.role, UserRole.STAFF_WRITER)

    def test_reactivated_staff_can_log_in_again(self):
        self.client.post(revoke_url(self.staff))
        self.client.post(reactivate_url(self.staff))

        self.client.force_authenticate(None)
        response = self.client.post(
            "/api/v1/auth/login/",
            {"email": "writer@example.com", "password": "testpass123"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_never_accepted_invite_cannot_be_reactivated(self):
        # Would otherwise produce an active account with no usable password.
        invitee = make_user(
            email="pending@example.com",
            role=UserRole.STAFF_WRITER,
            is_active=False,
            password=None,
        )

        response = self.client.post(reactivate_url(invitee))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        invitee.refresh_from_db()
        self.assertFalse(invitee.is_active)

    def test_reactivating_active_staff_rejected(self):
        response = self.client.post(reactivate_url(self.staff))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_non_super_admin_cannot_reactivate(self):
        self.client.post(revoke_url(self.staff))
        admin = make_user(email="admin@example.com", role=UserRole.ADMIN)
        self.client.force_authenticate(admin)

        response = self.client.post(reactivate_url(self.staff))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_activity_logged(self):
        self.client.post(revoke_url(self.staff))
        self.client.post(reactivate_url(self.staff))

        self.assertTrue(
            UserActivityLog.objects.filter(
                action=UserActivityActionEnums.STAFF_REACTIVATED
            ).exists()
        )

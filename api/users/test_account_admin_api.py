"""Admin actions on other people's accounts: reset password, delete, invite."""

from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.core import mail
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
from rest_framework_simplejwt.tokens import RefreshToken

from api.authentication.enums import TokenPurpose
from api.authentication.models import EmailVerificationToken
from api.courses.tests.factories import make_user
from api.payments.models import BankAccount
from api.users.enums import AccountStatus, UserRole
from api.users.models import User, UserActivityLog
from api.wallet.models import Wallet
from core.models import TransferOutboxEvent


def reset_url(user, audience):
    base = "auth/staff" if audience == "staff" else "users/admin"
    return f"/api/v1/{base}/{user.id}/send-password-reset/"


def erase_url(user, audience):
    base = "auth/staff" if audience == "staff" else "users/admin"
    return f"/api/v1/{base}/{user.id}/erase/"


class SendPasswordResetTests(APITestCase):
    def setUp(self):
        self.super_admin = make_user(
            role=UserRole.SUPER_ADMIN, status=AccountStatus.ACTIVE
        )
        self.admin = make_user(role=UserRole.ADMIN, status=AccountStatus.ACTIVE)

    def test_super_admin_sends_a_staff_member_a_reset_link(self):
        writer = make_user(role=UserRole.STAFF_WRITER, status=AccountStatus.ACTIVE)
        self.client.force_authenticate(self.super_admin)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reset_url(writer, "staff"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(
            EmailVerificationToken.objects.filter(
                user=writer, purpose=TokenPurpose.PASSWORD_RESET
            ).exists()
        )
        self.assertTrue(
            UserActivityLog.objects.filter(
                user=writer,
                actor_user=self.super_admin,
                action="PASSWORD_RESET_SENT_BY_ADMIN",
            ).exists()
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [writer.email])

    def test_admin_resets_non_staff_but_not_staff(self):
        creator = make_user(role=UserRole.COURSE_CREATOR, status=AccountStatus.ACTIVE)
        writer = make_user(role=UserRole.STAFF_WRITER, status=AccountStatus.ACTIVE)
        self.client.force_authenticate(self.admin)

        self.assertEqual(
            self.client.post(reset_url(creator, "teams")).status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(
            self.client.post(reset_url(writer, "staff")).status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_wrong_audience_is_404(self):
        creator = make_user(role=UserRole.COURSE_CREATOR, status=AccountStatus.ACTIVE)
        self.client.force_authenticate(self.super_admin)

        self.assertEqual(
            self.client.post(reset_url(creator, "staff")).status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_refusals_are_400(self):
        self.client.force_authenticate(self.admin)
        pending = make_user(role=UserRole.COURSE_CREATOR)
        User.objects.filter(id=pending.id).update(password="!unusable")
        suspended = make_user(
            role=UserRole.COURSE_CREATOR, status=AccountStatus.SUSPENDED
        )
        repeat = make_user(role=UserRole.COURSE_CREATOR, status=AccountStatus.ACTIVE)
        self.client.post(reset_url(repeat, "teams"))

        for user in (pending, suspended, repeat):
            with self.subTest(user=user.email):
                self.assertEqual(
                    self.client.post(reset_url(user, "teams")).status_code,
                    status.HTTP_400_BAD_REQUEST,
                )

    def test_creators_are_refused(self):
        self.client.force_authenticate(make_user(role=UserRole.COURSE_CREATOR))
        target = make_user(role=UserRole.COURSE_CREATOR, status=AccountStatus.ACTIVE)

        self.assertEqual(
            self.client.post(reset_url(target, "teams")).status_code,
            status.HTTP_403_FORBIDDEN,
        )


class EraseAccountTests(APITestCase):
    def setUp(self):
        self.super_admin = make_user(role=UserRole.SUPER_ADMIN)
        self.creator = make_user(
            role=UserRole.COURSE_CREATOR,
            status=AccountStatus.ACTIVE,
            first_name="Ada",
            phone_number="+2348000000000",
        )
        self.client.force_authenticate(self.super_admin)

    def erase(self, user, audience="teams", **overrides):
        payload = {"reason": "Requested by the user", "confirm_email": user.email}
        payload.update(overrides)
        return self.client.post(erase_url(user, audience), payload, format="json")

    def test_erasing_scrubs_personal_data_and_ends_access(self):
        BankAccount.objects.create(
            user=self.creator,
            bank_name="Bank",
            account_name="Ada",
            account_number="0123456789",
        )
        RefreshToken.for_user(self.creator)
        original_email = self.creator.email

        response = self.erase(self.creator)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.creator.refresh_from_db()
        self.assertNotEqual(self.creator.email, original_email)
        self.assertTrue(self.creator.email.endswith("@erased.invalid"))
        self.assertEqual(self.creator.phone_number, "")
        self.assertFalse(self.creator.is_active)
        self.assertEqual(self.creator.status, AccountStatus.DEACTIVATED)
        self.assertIsNotNone(self.creator.erased_at)
        self.assertFalse(self.creator.has_usable_password())
        self.assertFalse(
            BankAccount.objects.filter(user=self.creator, is_deleted=False).exists()
        )
        self.assertTrue(
            BlacklistedToken.objects.filter(token__user=self.creator).exists()
        )
        self.assertTrue(
            UserActivityLog.objects.filter(
                user=self.super_admin, action="ACCOUNT_ERASED"
            ).exists()
        )

    def test_an_erased_account_cannot_be_reinstated(self):
        self.erase(self.creator)

        response = self.client.post(f"/api/v1/users/admin/{self.creator.id}/reinstate/")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_confirmation_must_match(self):
        response = self.erase(self.creator, confirm_email="someone@example.com")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.creator.refresh_from_db()
        self.assertIsNone(self.creator.erased_at)

    def test_money_on_the_account_blocks_deletion(self):
        wallet = Wallet.objects.create(user=self.creator, balance=Decimal("10.00"))
        self.assertEqual(self.erase(self.creator).status_code, status.HTTP_409_CONFLICT)

        Wallet.objects.filter(id=wallet.id).update(balance=Decimal("0"))
        TransferOutboxEvent.objects.create(
            reference="TRF-1",
            user=self.creator,
            amount=Decimal("5.00"),
            recipient_code="RCP",
            status=TransferOutboxEvent.Status.PROCESSING,
        )
        self.assertEqual(self.erase(self.creator).status_code, status.HTTP_409_CONFLICT)

    def test_staff_accounts_go_through_the_staff_route(self):
        writer = make_user(role=UserRole.STAFF_WRITER)

        self.assertEqual(
            self.erase(writer, audience="teams").status_code, status.HTTP_404_NOT_FOUND
        )
        self.assertEqual(
            self.erase(writer, audience="staff").status_code, status.HTTP_200_OK
        )

    def test_admin_without_the_permission_is_refused(self):
        self.client.force_authenticate(make_user(role=UserRole.ADMIN))

        self.assertEqual(
            self.erase(self.creator).status_code, status.HTTP_403_FORBIDDEN
        )

    @override_settings(MFA_ENFORCED=True)
    def test_needs_a_challenged_mfa_session(self):
        user = User.objects.get(id=self.super_admin.id)
        self.client.force_authenticate(
            user, token={"mfa_verified": True, "role": user.role}
        )

        self.assertEqual(
            self.erase(self.creator).status_code, status.HTTP_403_FORBIDDEN
        )

    def test_query_count_does_not_grow_with_sessions(self):
        ContentType.objects.get_for_model(User)

        def count(sessions):
            target = make_user(role=UserRole.COURSE_CREATOR)
            for _ in range(sessions):
                RefreshToken.for_user(target)
            with CaptureQueriesContext(connection) as ctx:
                self.erase(target)
            return len(ctx.captured_queries)

        self.assertEqual(count(1), count(3))


class TeamInvitationTests(APITestCase):
    URL = "/api/v1/users/admin/invitations/"

    def test_admin_invites_a_creator_reviewer(self):
        self.client.force_authenticate(make_user(role=UserRole.ADMIN))

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                self.URL,
                {
                    "email": "reviewer@example.com",
                    "first_name": "Ada",
                    "last_name": "Obi",
                },
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        invitee = User.objects.get(email="reviewer@example.com")
        self.assertEqual(invitee.role, UserRole.CREATOR_REVIEWER)
        self.assertEqual(invitee.access_role.system_key, UserRole.CREATOR_REVIEWER)
        self.assertFalse(invitee.is_active)
        self.assertEqual(len(mail.outbox), 1)

    def test_existing_account_is_400(self):
        existing = make_user(role=UserRole.COURSE_CREATOR)
        self.client.force_authenticate(make_user(role=UserRole.ADMIN))

        response = self.client.post(
            self.URL,
            {"email": existing.email, "first_name": "A", "last_name": "B"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_writer_is_refused(self):
        self.client.force_authenticate(make_user(role=UserRole.STAFF_WRITER))

        response = self.client.post(
            self.URL,
            {"email": "x@example.com", "first_name": "A", "last_name": "B"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

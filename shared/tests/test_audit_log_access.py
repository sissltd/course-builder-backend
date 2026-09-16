"""Who may read the platform-wide audit trail.

It lists every user's sign-in events - emails and IPs included - so it is
gated by the app's own Admin tier. It used Django's is_staff flag, which
only the bootstrapped Super Admin carries, so invited Admins were locked
out; these tests pin the role-based gate in place.
"""

from rest_framework import status
from rest_framework.test import APITestCase

from api.courses.tests.factories import make_user
from api.users.enums import UserRole
from shared.audit.models import AuditLog

URL = "/api/v1/logs"


class AuditLogAccessTests(APITestCase):
    def setUp(self):
        AuditLog.objects.create(
            event=AuditLog.Event.OTP_REQUESTED, email="someone@example.com"
        )

    def _list_as(self, role):
        self.client.force_authenticate(make_user(role=role))
        return self.client.get(URL)

    def test_admin_tier_reads_every_users_entries(self):
        for role in (UserRole.ADMIN, UserRole.SUPER_ADMIN):
            with self.subTest(role=role):
                response = self._list_as(role)

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                emails = [row["email"] for row in response.data["data"]["results"]]
                self.assertEqual(emails, ["someone@example.com"])

    def test_roles_outside_the_admin_tier_are_refused(self):
        for role in (
            UserRole.STAFF_APPROVER,
            UserRole.CREATOR_REVIEWER,
            UserRole.COURSE_CREATOR,
        ):
            with self.subTest(role=role):
                self.assertEqual(
                    self._list_as(role).status_code, status.HTTP_403_FORBIDDEN
                )

    def test_anonymous_caller_is_refused(self):
        self.assertEqual(
            self.client.get(URL).status_code, status.HTTP_401_UNAUTHORIZED
        )

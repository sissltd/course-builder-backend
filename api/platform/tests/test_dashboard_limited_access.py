"""Dashboard Limited Access: the admin dashboards without money figures."""

from rest_framework import status
from rest_framework.test import APITestCase

from api.authorization import codenames as c
from api.authorization.models import Role, RolePermission
from api.courses.tests.factories import make_user
from api.users.enums import UserRole
from api.users.models import User


def _limited_viewer():
    role = Role.objects.create(name="Board observer", base_role=UserRole.STAFF_WRITER)
    RolePermission.objects.create(role=role, codename=c.DASHBOARD_VIEW_LIMITED)
    user = make_user(role=UserRole.STAFF_WRITER)
    User.objects.filter(id=user.id).update(access_role=role)
    return User.objects.get(id=user.id)


class DashboardLimitedAccessTests(APITestCase):
    def test_overview_hides_money_for_limited_access(self):
        self.client.force_authenticate(_limited_viewer())

        data = self.client.get("/api/v1/admin/overview/").data

        self.assertFalse(data["financials_included"])
        self.assertIsNone(data["wallet_totals"])
        self.assertIsNone(data["withdrawals"])
        self.assertIsNone(data["cost_trend"])
        self.assertIsNone(data["today"]["daily_cost"])
        self.assertIsNone(data["today"]["avg_cost_per_course"])
        self.assertIsNotNone(data["courses"])

    def test_analytics_hides_money_for_limited_access(self):
        self.client.force_authenticate(_limited_viewer())

        response = self.client.get("/api/v1/admin/analytics/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["financials_included"])
        self.assertIsNone(response.data["cost"])
        self.assertIsNone(response.data["earnings"])
        self.assertIsNone(response.data["kpis"]["cost_per_course"])

    def test_view_only_sees_everything(self):
        self.client.force_authenticate(make_user(role=UserRole.ADMIN))

        overview = self.client.get("/api/v1/admin/overview/").data
        analytics = self.client.get("/api/v1/admin/analytics/").data

        self.assertTrue(overview["financials_included"])
        self.assertIsNotNone(overview["wallet_totals"])
        self.assertTrue(analytics["financials_included"])
        self.assertIsNotNone(analytics["cost"])

    def test_neither_permission_is_refused(self):
        self.client.force_authenticate(make_user(role=UserRole.STAFF_WRITER))

        self.assertEqual(
            self.client.get("/api/v1/admin/overview/").status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self.client.get("/api/v1/admin/analytics/").status_code,
            status.HTTP_403_FORBIDDEN,
        )

from decimal import Decimal

from rest_framework import status
from rest_framework.test import APITestCase

from django.contrib.auth import get_user_model

from api.authentication.tests.factories import make_user
from api.courses.enums import CourseStatus
from api.courses.services import course_validation_service
from api.courses.tests.factories import build_compliant_course, make_category
from api.platform.models import PlatformSettings
from api.platform.services import platform_settings_service
from api.users.enums import AccountStatus, UserRole
from api.wallet.services import wallet_service

User = get_user_model()


class PlatformSettingsServiceTests(APITestCase):
    def test_get_settings_creates_defaults_matching_old_env_values(self):
        settings_row = platform_settings_service.get_settings()

        self.assertEqual(settings_row.course_module_count_min, 4)
        self.assertEqual(settings_row.course_module_count_max, 12)
        self.assertEqual(settings_row.minimum_withdrawal_threshold, Decimal("50.00"))
        self.assertEqual(PlatformSettings.objects.count(), 1)

    def test_get_settings_returns_same_row(self):
        first = platform_settings_service.get_settings()
        second = platform_settings_service.get_settings()

        self.assertEqual(first.id, second.id)

    def test_update_only_touches_provided_fields(self):
        platform_settings_service.update_settings(course_module_count_min=6)
        result = platform_settings_service.get_settings()

        self.assertEqual(result.course_module_count_min, 6)
        self.assertEqual(result.course_module_count_max, 12)


class PlatformSettingsApiTests(APITestCase):
    def setUp(self):
        self.admin = make_user(role=UserRole.ADMIN)
        self.creator = make_user(role=UserRole.COURSE_CREATOR)

    def test_get_requires_authentication(self):
        response = self.client.get("/api/v1/platform-settings/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_any_authenticated_user_can_read(self):
        self.client.force_authenticate(self.creator)

        response = self.client.get("/api/v1/platform-settings/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["course_module_count_min"], 4)

    def test_creator_cannot_patch(self):
        self.client.force_authenticate(self.creator)

        response = self.client.patch(
            "/api/v1/platform-settings/",
            {"course_module_count_min": 6},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_patch_one_field(self):
        self.client.force_authenticate(self.admin)

        response = self.client.patch(
            "/api/v1/platform-settings/",
            {"course_module_count_min": 6},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["course_module_count_min"], 6)
        self.assertEqual(response.data["course_module_count_max"], 12)

    def test_patch_empty_body_rejected(self):
        self.client.force_authenticate(self.admin)

        response = self.client.patch(
            "/api/v1/platform-settings/", {}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_course_validation_reflects_updated_threshold(self):
        """Regression: confirm course_validation_service actually reads the
        DB value, not a cached/stale one, after a PATCH changes it."""

        category = make_category()
        course = build_compliant_course(category=category, module_count=4)
        self.assertEqual(
            course_validation_service.validate_structural_standards(course), []
        )

        self.client.force_authenticate(self.admin)
        self.client.patch(
            "/api/v1/platform-settings/",
            {"course_module_count_min": 5},
            format="json",
        )

        failures = course_validation_service.validate_structural_standards(course)
        self.assertTrue(
            any("modules" in failure for failure in failures),
            failures,
        )


class AdminOverviewApiTests(APITestCase):
    def setUp(self):
        self.admin = make_user(role=UserRole.ADMIN)
        self.creator = make_user(role=UserRole.COURSE_CREATOR)

    def test_requires_authentication(self):
        response = self.client.get("/api/v1/admin/overview/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_creator_is_forbidden(self):
        self.client.force_authenticate(self.creator)

        response = self.client.get("/api/v1/admin/overview/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_gets_every_status_key_including_zeroes(self):
        """The dashboard renders a fixed set of tiles, so a status with no rows
        must still be present rather than vanishing from the payload."""

        self.client.force_authenticate(self.admin)

        response = self.client.get("/api/v1/admin/overview/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(set(response.data["users"]), set(AccountStatus.values))
        self.assertEqual(set(response.data["courses"]), set(CourseStatus.values))
        self.assertEqual(response.data["courses"][CourseStatus.PUBLISHED], 0)

    def test_counts_reflect_real_rows(self):
        self.client.force_authenticate(self.admin)

        response = self.client.get("/api/v1/admin/overview/")
        user_counts = response.data["users"]
        self.assertEqual(sum(user_counts.values()), User.objects.count())

    def test_wallet_totals_reported(self):
        wallet_service.credit_wallet(user=self.creator, amount=Decimal("120.00"))
        self.client.force_authenticate(self.admin)

        response = self.client.get("/api/v1/admin/overview/")
        totals = response.data["wallet_totals"]
        self.assertEqual(totals["balance_held"], "120.00")
        self.assertEqual(totals["total_credited"], "120.00")
        self.assertEqual(totals["awaiting_payout"], "0.00")

"""Three difficulty-keyed payout levels, archiving, and the header stats.

The load-bearing test here is that a course's payout follows its own
difficulty. Getting that wrong pays creators the wrong amount, which is
the single most expensive way this change could fail.
"""

from decimal import Decimal

from rest_framework import status
from rest_framework.test import APITestCase

from api.catalog.enums import CategoryStatus
from api.catalog.models import Category
from api.courses.enums import CourseStatus, DifficultyLevel
from api.courses.tests.factories import (
    build_compliant_course,
    make_category,
    make_user,
)
from api.users.enums import UserRole

LIST_URL = "/api/v1/categories/"
STATS_URL = "/api/v1/categories/stats/"


class CategoryPriceLevelTests(APITestCase):
    def test_price_for_maps_each_difficulty_to_its_own_tier(self):
        category = make_category(
            creator_price_beginner=Decimal("500.00"),
            creator_price_intermediate=Decimal("520.00"),
            creator_price_advanced=Decimal("600.00"),
        )

        self.assertEqual(
            category.price_for(DifficultyLevel.BEGINNER), Decimal("500.00")
        )
        self.assertEqual(
            category.price_for(DifficultyLevel.INTERMEDIATE), Decimal("520.00")
        )
        self.assertEqual(
            category.price_for(DifficultyLevel.ADVANCED), Decimal("600.00")
        )

    def test_unknown_difficulty_falls_back_to_the_entry_rate(self):
        """A payout must always resolve to a number, and erring low is the
        safe direction."""

        category = make_category(
            creator_price_beginner=Decimal("500.00"),
            creator_price_advanced=Decimal("600.00"),
        )

        self.assertEqual(category.price_for(None), Decimal("500.00"))
        self.assertEqual(category.price_for("NOT_A_LEVEL"), Decimal("500.00"))


class SubmissionSnapshotTests(APITestCase):
    """The payout a course freezes at submission must match its difficulty."""

    def _submit(self, difficulty):
        category = make_category(
            creator_price_beginner=Decimal("500.00"),
            creator_price_intermediate=Decimal("520.00"),
            creator_price_advanced=Decimal("600.00"),
        )
        course = build_compliant_course(category=category)
        course.difficulty_level = difficulty
        course.topic = None
        course.save(update_fields=["difficulty_level", "topic"])

        self.client.force_authenticate(course.creator)
        response = self.client.post(f"/api/v1/courses/{course.id}/submit/")
        course.refresh_from_db()
        return response, course

    def test_advanced_course_snapshots_the_advanced_rate(self):
        response, course = self._submit(DifficultyLevel.ADVANCED)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(course.status, CourseStatus.SUBMITTED)
        self.assertEqual(course.creator_price_snapshot, Decimal("600.00"))

    def test_beginner_course_snapshots_the_beginner_rate(self):
        _response, course = self._submit(DifficultyLevel.BEGINNER)

        self.assertEqual(course.creator_price_snapshot, Decimal("500.00"))

    def test_intermediate_course_snapshots_the_intermediate_rate(self):
        _response, course = self._submit(DifficultyLevel.INTERMEDIATE)

        self.assertEqual(course.creator_price_snapshot, Decimal("520.00"))


class CategoryArchiveTests(APITestCase):
    def setUp(self):
        self.admin = make_user(role=UserRole.ADMIN)
        self.client.force_authenticate(self.admin)
        self.category = make_category()

    def _post(self, action, category=None):
        target = category or self.category
        return self.client.post(f"/api/v1/categories/{target.id}/{action}/")

    def test_archiving_sets_the_status_without_deleting(self):
        response = self._post("archive")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.category.refresh_from_db()
        self.assertEqual(self.category.status, CategoryStatus.ARCHIVED)
        self.assertTrue(Category.objects.filter(pk=self.category.pk).exists())

    def test_archiving_twice_is_a_400_not_a_silent_success(self):
        self._post("archive")

        again = self._post("archive")

        self.assertEqual(again.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unarchive_restores_to_active(self):
        self._post("archive")

        response = self._post("unarchive")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.category.refresh_from_db()
        self.assertEqual(self.category.status, CategoryStatus.ACTIVE)

    def test_unarchiving_a_live_category_is_refused(self):
        self.assertEqual(
            self._post("unarchive").status_code, status.HTTP_400_BAD_REQUEST
        )

    def test_creator_cannot_archive(self):
        self.client.force_authenticate(make_user(role=UserRole.COURSE_CREATOR))

        response = self._post("archive")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.category.refresh_from_db()
        self.assertEqual(self.category.status, CategoryStatus.ACTIVE)


class CategoryStatsTests(APITestCase):
    def setUp(self):
        self.client.force_authenticate(make_user(role=UserRole.ADMIN))

    def test_counts_move_with_the_categories_created(self):
        """Asserted as a delta: the platform seeds categories on migrate."""

        before = self.client.get(STATS_URL).data

        make_category(status=CategoryStatus.ACTIVE)
        make_category(status=CategoryStatus.ACTIVE)
        make_category(status=CategoryStatus.ARCHIVED)

        after = self.client.get(STATS_URL).data

        self.assertEqual(after["total"] - before["total"], 3)
        self.assertEqual(after["active"] - before["active"], 2)
        self.assertEqual(after["archived"] - before["archived"], 1)

    def test_every_bucket_is_always_present(self):
        data = self.client.get(STATS_URL).data

        self.assertEqual(
            set(data), {"total", "active", "inactive", "archived"}
        )
        self.assertEqual(
            data["total"],
            data["active"] + data["inactive"] + data["archived"],
        )


class CategoryListShapeTests(APITestCase):
    def setUp(self):
        self.client.force_authenticate(make_user(role=UserRole.COURSE_CREATOR))

    def test_list_exposes_the_three_tiers_and_icon(self):
        category = make_category(icon="rocket")

        row = {
            r["name"]: r
            for r in self.client.get(
                f"{LIST_URL}?search={category.name}"
            ).data["data"]["results"]
        }.get(category.name) or self.client.get(
            f"/api/v1/categories/{category.id}/"
        ).data

        for field in (
            "creator_price_beginner",
            "creator_price_intermediate",
            "creator_price_advanced",
            "icon",
            "total_courses",
        ):
            self.assertIn(field, row)
        self.assertEqual(row["icon"], "rocket")

    def test_total_courses_counts_courses_in_the_category(self):
        category = make_category()
        build_compliant_course(category=category)

        rows = {
            row["name"]: row
            for row in self.client.get(LIST_URL).data["data"]["results"]
        }

        self.assertEqual(rows[category.name]["total_courses"], 1)

    def test_archived_categories_are_filterable(self):
        make_category(status=CategoryStatus.ARCHIVED, name="Retired")
        make_category(status=CategoryStatus.ACTIVE, name="Live")

        names = [
            row["name"]
            for row in self.client.get(
                f"{LIST_URL}?status={CategoryStatus.ARCHIVED}"
            ).data["data"]["results"]
        ]

        self.assertEqual(names, ["Retired"])

"""Three creator prices by how a course is made, archiving, and the header
stats.

The load-bearing tests here are that a course locks the right prices at
submission and is priced at the one of its type. Getting that wrong pays
creators the wrong amount, which is the most expensive way this could fail.
"""

from decimal import Decimal

from rest_framework import status
from rest_framework.test import APITestCase

from api.catalog.enums import CategoryStatus, CreatorPriceType
from api.catalog.models import Category
from api.courses.enums import CourseSourceType, CourseStatus
from api.courses.tests.factories import (
    build_compliant_course,
    make_category,
    make_topic,
    make_user,
)
from api.users.enums import UserRole

LIST_URL = "/api/v1/categories/"
STATS_URL = "/api/v1/categories/stats/"

PRICES = {
    "creator_price_without_video": Decimal("400.00"),
    "creator_price_with_video": Decimal("600.00"),
    "creator_price_ai_built": Decimal("250.00"),
}


class CreatorPriceTests(APITestCase):
    def test_each_price_type_reads_its_own_price(self):
        category = make_category(**PRICES)

        self.assertEqual(category.price_for(CreatorPriceType.WITHOUT_VIDEO), Decimal("400.00"))
        self.assertEqual(category.price_for(CreatorPriceType.WITH_VIDEO), Decimal("600.00"))
        self.assertEqual(category.price_for(CreatorPriceType.AI_BUILT), Decimal("250.00"))

    def test_a_topic_overrides_only_the_prices_it_sets(self):
        category = make_category(**PRICES)
        topic = make_topic(
            category=category,
            creator_price_without_video=None,
            creator_price_with_video=Decimal("700.00"),
            creator_price_ai_built=None,
        )

        self.assertEqual(topic.price_for(CreatorPriceType.WITH_VIDEO), Decimal("700.00"))
        self.assertEqual(topic.price_for(CreatorPriceType.WITHOUT_VIDEO), Decimal("400.00"))
        self.assertEqual(topic.price_for(CreatorPriceType.AI_BUILT), Decimal("250.00"))


class SubmissionLocksPricesTests(APITestCase):
    """At submission a course locks all three prices and is priced at the
    one of its type."""

    def _submit(self, **fields):
        course = build_compliant_course(category=make_category(**PRICES))
        course.topic = None
        for name, value in fields.items():
            setattr(course, name, value)
        course.save()

        self.client.force_authenticate(course.creator)
        response = self.client.post(f"/api/v1/courses/{course.id}/submit/")
        course.refresh_from_db()
        return response, course

    def test_a_creators_own_course_is_priced_with_video_and_locks_all_three(self):
        response, course = self._submit()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(course.status, CourseStatus.SUBMITTED)
        self.assertEqual(course.payout_type, CreatorPriceType.WITH_VIDEO)
        self.assertEqual(course.creator_price_snapshot, Decimal("600.00"))
        self.assertEqual(
            course.creator_rates_snapshot,
            {"WITHOUT_VIDEO": "400.00", "WITH_VIDEO": "600.00", "AI_BUILT": "250.00"},
        )
        self.assertIsNone(course.creator_paid_at)

    def test_an_ai_built_course_is_priced_at_the_ai_price(self):
        _response, course = self._submit(source_type=CourseSourceType.AI_GENERATED)

        self.assertEqual(course.payout_type, CreatorPriceType.AI_BUILT)
        self.assertEqual(course.creator_price_snapshot, Decimal("250.00"))

    def test_a_later_price_change_does_not_touch_a_locked_course(self):
        _response, course = self._submit()

        Category.objects.filter(pk=course.category_id).update(creator_price_with_video=Decimal("999.00"))

        course.refresh_from_db()
        self.assertEqual(course.creator_price_snapshot, Decimal("600.00"))


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
    """The full list (the creator prices, ARCHIVED filter) is an Admin Writer
    surface; creators never see it - only the picker payload."""

    def setUp(self):
        self.client.force_authenticate(make_user(role=UserRole.STAFF_WRITER))

    def test_list_exposes_the_three_prices_and_icon(self):
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
            "creator_price_without_video",
            "creator_price_with_video",
            "creator_price_ai_built",
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

"""Force Course Version Migration: moving unpublished courses to another version."""

from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework import status
from rest_framework.test import APITestCase

from api.courses.enums import CourseStatus
from api.courses.models import Course, CourseVersion
from api.courses.tests.factories import make_draft_course, make_user
from api.users.enums import UserRole
from api.users.models import UserActivityLog

LIST_URL = "/api/v1/admin/course-versions/"
MIGRATE_URL = "/api/v1/admin/course-versions/migrations/"


class CourseVersionMigrationTests(APITestCase):
    def setUp(self):
        self.old = CourseVersion.objects.create(label="0.9", is_active=False)
        self.new = CourseVersion.objects.create(label="2.0", is_active=True)
        self.admin = make_user(role=UserRole.ADMIN)
        self.client.force_authenticate(self.admin)

    def migrate(self, **overrides):
        payload = {
            "from_version_id": str(self.old.id),
            "to_version_id": str(self.new.id),
        }
        payload.update(overrides)
        return self.client.post(MIGRATE_URL, payload, format="json")

    def test_unpublished_courses_move_and_published_stay(self):
        draft = make_draft_course(version=self.old)
        submitted = make_draft_course(version=self.old, status=CourseStatus.SUBMITTED)
        published = make_draft_course(version=self.old, status=CourseStatus.PUBLISHED)

        response = self.migrate()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data["data"]
        self.assertEqual(data["courses_moved"], 2)
        self.assertEqual(data["courses_by_status"], {"DRAFT": 1, "SUBMITTED": 1})
        self.assertFalse(data["dry_run"])
        for course, version in (
            (draft, self.new),
            (submitted, self.new),
            (published, self.old),
        ):
            course.refresh_from_db()
            self.assertEqual(course.version, version)
        self.assertEqual(
            UserActivityLog.objects.filter(
                user=self.admin, action="COURSE_VERSION_MIGRATED"
            ).count(),
            2,
        )

    def test_dry_run_changes_nothing(self):
        draft = make_draft_course(version=self.old)

        response = self.migrate(dry_run=True)

        self.assertEqual(response.data["data"]["courses_moved"], 1)
        self.assertTrue(response.data["data"]["dry_run"])
        draft.refresh_from_db()
        self.assertEqual(draft.version, self.old)

    def test_target_must_be_active_and_different(self):
        self.assertEqual(
            self.migrate(to_version_id=str(self.old.id)).status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        retired = CourseVersion.objects.create(label="1.5", is_active=False)
        self.assertEqual(
            self.migrate(to_version_id=str(retired.id)).status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    def test_unknown_version_is_404(self):
        response = self.migrate(to_version_id="00000000-0000-0000-0000-000000000000")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_list_counts_courses_per_version(self):
        make_draft_course(version=self.old)
        make_draft_course(version=self.old, status=CourseStatus.PUBLISHED)

        rows = {row["label"]: row for row in self.client.get(LIST_URL).data["data"]}

        self.assertEqual(rows["0.9"]["migratable_count"], 1)
        self.assertEqual(rows["0.9"]["published_count"], 1)
        self.assertFalse(rows["0.9"]["is_active"])

    def test_admin_can_create_course_version(self):
        response = self.client.post(
            LIST_URL,
            {"label": "3.0", "is_active": True},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], 201)
        self.assertEqual(response.data["data"]["label"], "3.0")
        self.assertTrue(response.data["data"]["is_active"])
        self.assertTrue(CourseVersion.objects.filter(label="3.0").exists())

    def test_duplicate_course_version_label_is_rejected(self):
        response = self.client.post(
            LIST_URL,
            {"label": self.old.label},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["errors"][0]["field_name"], "label")

    def test_admin_can_update_course_version(self):
        response = self.client.patch(
            f"{LIST_URL}{self.old.id}/",
            {"label": "0.9.1", "is_active": True},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], 200)
        self.assertEqual(response.data["data"]["label"], "0.9.1")
        self.assertTrue(response.data["data"]["is_active"])
        self.old.refresh_from_db()
        self.assertEqual(self.old.label, "0.9.1")

    def test_cannot_retire_the_last_active_version(self):
        CourseVersion.objects.exclude(pk=self.old.id).update(is_active=False)
        self.old.is_active = True
        self.old.save(update_fields=["is_active"])

        response = self.client.patch(
            f"{LIST_URL}{self.old.id}/",
            {"is_active": False},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("is_active", response.data["errors"][0]["field_name"])
        self.old.refresh_from_db()
        self.assertTrue(self.old.is_active)

    def test_version_management_requires_admin_permission(self):
        for role, expected in (
            (UserRole.STAFF_APPROVER, status.HTTP_201_CREATED),
            (UserRole.SUPER_ADMIN, status.HTTP_201_CREATED),
            (UserRole.STAFF_WRITER, status.HTTP_403_FORBIDDEN),
            (UserRole.CREATOR_REVIEWER, status.HTTP_403_FORBIDDEN),
        ):
            with self.subTest(role=role):
                self.client.force_authenticate(make_user(role=role))
                response = self.client.post(
                    LIST_URL,
                    {"label": f"4.{len(CourseVersion.objects.all())}"},
                    format="json",
                )
                self.assertEqual(response.status_code, expected)

    def test_who_may_migrate(self):
        for role, expected in (
            (UserRole.STAFF_APPROVER, status.HTTP_200_OK),
            (UserRole.SUPER_ADMIN, status.HTTP_200_OK),
            (UserRole.STAFF_WRITER, status.HTTP_403_FORBIDDEN),
            (UserRole.CREATOR_REVIEWER, status.HTTP_403_FORBIDDEN),
        ):
            with self.subTest(role=role):
                self.client.force_authenticate(make_user(role=role))
                self.assertEqual(self.migrate(dry_run=True).status_code, expected)
                self.assertEqual(self.client.get(LIST_URL).status_code, expected)

    def test_unauthenticated_is_refused(self):
        self.client.force_authenticate(None)

        self.assertEqual(self.migrate().status_code, status.HTTP_401_UNAUTHORIZED)

    def test_query_count_does_not_grow_with_courses(self):
        # ContentType lookups are cached in-process after the first; warm it so
        # the comparison measures growth per course, not first-use caching.
        ContentType.objects.get_for_model(Course)

        def count(n):
            for _ in range(n):
                make_draft_course(version=self.old)
            with CaptureQueriesContext(connection) as ctx:
                self.migrate()
            return len(ctx.captured_queries)

        self.assertEqual(count(1), count(5))

"""0006 grants the Writer courses.approve / courses.reject; it is additive,
idempotent and reversible."""

import importlib

from django.apps import apps
from django.test import TestCase

from api.authorization.models import RolePermission

migration = importlib.import_module(
    "api.authorization.migrations.0006_grant_writer_course_review"
)


def _writer_grants():
    return set(
        RolePermission.objects.filter(
            role__system_key="STAFF_WRITER", codename__in=migration.CODENAMES
        ).values_list("codename", flat=True)
    )


class WriterReviewGrantMigrationTests(TestCase):
    def test_grant_is_applied_and_idempotent(self):
        migration.revoke(apps, None)
        self.assertEqual(_writer_grants(), set())

        migration.grant(apps, None)
        migration.grant(apps, None)

        self.assertEqual(_writer_grants(), set(migration.CODENAMES))
        self.assertEqual(
            RolePermission.objects.filter(
                role__system_key="STAFF_WRITER", codename="courses.approve"
            ).count(),
            1,
        )

    def test_revoke_removes_only_the_writers_grants(self):
        migration.grant(apps, None)
        others_before = RolePermission.objects.filter(
            codename__in=migration.CODENAMES
        ).exclude(role__system_key="STAFF_WRITER").count()

        migration.revoke(apps, None)

        self.assertEqual(_writer_grants(), set())
        self.assertEqual(
            RolePermission.objects.filter(codename__in=migration.CODENAMES)
            .exclude(role__system_key="STAFF_WRITER")
            .count(),
            others_before,
        )

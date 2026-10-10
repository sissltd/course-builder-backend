"""0007 grants `production.manage` to Admin and Super Admin; additive,
idempotent and reversible."""

import importlib

from django.apps import apps
from django.test import TestCase

from api.authorization.models import RolePermission

migration = importlib.import_module("api.authorization.migrations.0007_grant_production_manage")


def _holders():
    return set(
        RolePermission.objects.filter(codename="production.manage").values_list(
            "role__system_key", flat=True
        )
    )


class ProductionManageGrantMigrationTests(TestCase):
    def test_grant_is_applied_idempotently_and_reverses(self):
        migration.revoke(apps, None)
        self.assertEqual(_holders(), set())

        migration.grant(apps, None)
        migration.grant(apps, None)
        self.assertEqual(_holders(), {"ADMIN", "SUPER_ADMIN"})
        self.assertEqual(RolePermission.objects.filter(codename="production.manage").count(), 2)

        migration.revoke(apps, None)
        self.assertEqual(_holders(), set())

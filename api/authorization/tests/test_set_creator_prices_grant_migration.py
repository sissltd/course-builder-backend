"""0008 grants `catalog.set_creator_prices` to Writer, Admin and Super Admin;
additive, idempotent and reversible."""

import importlib

from django.apps import apps
from django.test import TestCase

from api.authorization.models import RolePermission

migration = importlib.import_module("api.authorization.migrations.0008_grant_set_creator_prices")
CODENAME = "catalog.set_creator_prices"


def _holders():
    return set(RolePermission.objects.filter(codename=CODENAME).values_list("role__system_key", flat=True))


class SetCreatorPricesGrantMigrationTests(TestCase):
    def test_grant_is_applied_idempotently_and_reverses(self):
        migration.revoke(apps, None)
        self.assertEqual(_holders(), set())

        migration.grant(apps, None)
        migration.grant(apps, None)
        self.assertEqual(_holders(), {"STAFF_WRITER", "ADMIN", "SUPER_ADMIN"})
        self.assertEqual(RolePermission.objects.filter(codename=CODENAME).count(), 3)

        migration.revoke(apps, None)
        self.assertEqual(_holders(), set())

"""Grant `catalog.set_creator_prices` to its default holders (Writer, Admin, Super Admin).

Frozen copy of the defaults at the time the permission was introduced; it
must not import the live registry. Only adds grants, so re-running it or
running it after an admin has edited a role never removes anything. The
reverse removes exactly this codename.
"""

from django.db import migrations

NEW_GRANTS = {
    "catalog.set_creator_prices": ["STAFF_WRITER", "ADMIN", "SUPER_ADMIN"],
}


def grant(apps, schema_editor):
    Role = apps.get_model("authorization", "Role")
    RolePermission = apps.get_model("authorization", "RolePermission")
    roles = dict(
        Role.objects.filter(system_key__isnull=False).values_list("system_key", "id")
    )
    RolePermission.objects.bulk_create(
        [
            RolePermission(role_id=roles[key], codename=codename)
            for codename, keys in NEW_GRANTS.items()
            for key in keys
            if key in roles
        ],
        ignore_conflicts=True,
    )


def revoke(apps, schema_editor):
    apps.get_model("authorization", "RolePermission").objects.filter(
        codename__in=list(NEW_GRANTS)
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("authorization", "0007_grant_production_manage"),
    ]

    operations = [
        migrations.RunPython(grant, revoke),
    ]

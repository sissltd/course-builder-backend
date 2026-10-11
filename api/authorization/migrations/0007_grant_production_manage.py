"""Grant `production.manage` to its default holders (Admin, Super Admin).

Frozen copy of the defaults at the time the permission was introduced; it
must not import the live registry. Only adds grants, so re-running it or
running it after an admin has edited a role never removes anything. The
reverse removes exactly this codename.
"""

from django.db import migrations

NEW_GRANTS = {
    "production.manage": ["ADMIN", "SUPER_ADMIN"],
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
        ("authorization", "0006_grant_writer_course_review"),
    ]

    operations = [
        migrations.RunPython(grant, revoke),
    ]

"""Grant the permissions added with the admin features to their default holders.

Frozen copy of the defaults at the time these permissions were introduced;
it must not import the live registry. Only adds grants, so re-running it or
running it after an admin has edited a role never removes anything.
"""

from django.db import migrations

NEW_GRANTS = {
    "dashboard.view_limited": ["SUPER_ADMIN"],
    "courses.force_version_migration": ["ADMIN", "STAFF_APPROVER", "SUPER_ADMIN"],
    "staff.delete": ["SUPER_ADMIN"],
    "staff.reset_password": ["SUPER_ADMIN"],
    "creators.issue_refund": ["SUPER_ADMIN"],
    "teams.invite": ["ADMIN", "SUPER_ADMIN"],
    "teams.delete_account": ["SUPER_ADMIN"],
    "teams.reset_password": ["ADMIN", "SUPER_ADMIN"],
}


def grant(apps, schema_editor):
    Role = apps.get_model("authorization", "Role")
    RolePermission = apps.get_model("authorization", "RolePermission")
    roles = dict(Role.objects.filter(system_key__isnull=False).values_list("system_key", "id"))
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
        ("authorization", "0003_remove_role_version"),
    ]

    operations = [
        migrations.RunPython(grant, revoke),
    ]

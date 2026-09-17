"""Point every existing user at the built-in role for their `role`.

One UPDATE per role value, so the cost does not grow with the user count.
Reversing clears the link; the column is still nullable at that point.
"""

from django.db import migrations


def backfill(apps, schema_editor):
    User = apps.get_model("users", "User")
    Role = apps.get_model("authorization", "Role")
    for role_id, system_key in Role.objects.filter(system_key__isnull=False).values_list(
        "id", "system_key"
    ):
        User.objects.filter(role=system_key, access_role__isnull=True).update(
            access_role_id=role_id
        )


def clear(apps, schema_editor):
    apps.get_model("users", "User").objects.update(access_role_id=None)


class Migration(migrations.Migration):
    dependencies = [
        ("authorization", "0002_seed_system_roles"),
        ("users", "0037_user_access_role_alter_user_role"),
    ]

    operations = [
        migrations.RunPython(backfill, clear),
    ]

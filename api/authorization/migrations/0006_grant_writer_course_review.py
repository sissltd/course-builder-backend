"""Let the Writer decide courses at the review seats.

With the staged review flow on, Writers read the text at the first seat and
watch the video at the second (review_service.STAGED_SEAT_ROLES), so they need
`courses.approve` and `courses.reject` to decide them. The grants are additive
and inert while the flow is off: a seat's role, not the permission, decides who
may sit it.

Frozen copy of the defaults at the time; it must not import the live registry.
Only adds grants, so re-running it or running it after an admin has edited a
role never removes anything. Reversing removes exactly the grants added here.
"""

from django.db import migrations

ROLE_KEY = "STAFF_WRITER"
CODENAMES = ["courses.approve", "courses.reject"]


def grant(apps, schema_editor):
    Role = apps.get_model("authorization", "Role")
    RolePermission = apps.get_model("authorization", "RolePermission")
    role = Role.objects.filter(system_key=ROLE_KEY).first()
    if role is None:
        return
    RolePermission.objects.bulk_create(
        [RolePermission(role_id=role.id, codename=codename) for codename in CODENAMES],
        ignore_conflicts=True,
    )


def revoke(apps, schema_editor):
    apps.get_model("authorization", "RolePermission").objects.filter(
        role__system_key=ROLE_KEY, codename__in=CODENAMES
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("authorization", "0005_grant_support_manage_requests"),
    ]

    operations = [
        migrations.RunPython(grant, revoke),
    ]

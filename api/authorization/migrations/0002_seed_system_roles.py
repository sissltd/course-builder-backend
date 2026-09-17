"""Create the built-in role for every UserRole, with today's access.

The grant table is a frozen copy taken when permissions became stored: each
role holds exactly the capabilities its role-class gates admitted. It must
not import the live registry - a later registry change would silently rewrite
what this historical migration did. `SystemRoleSeedDriftTests` keeps later
additions honest by requiring a backfill migration for every new codename.
"""

from django.db import migrations

ROLE_LABELS = {
    "COURSE_CREATOR": "Course Creator",
    "CREATOR_REVIEWER": "Creator Reviewer",
    "STAFF_WRITER": "Writer",
    "STAFF_VERIFIER": "Verifier",
    "STAFF_APPROVER": "Approver",
    "AI_REVIEWER": "AI Reviewer",
    "QA_REVIEWER": "QA Reviewer",
    "ADMIN": "Admin",
    "SUPER_ADMIN": "Super Admin",
}

SYSTEM_ROLE_GRANTS = {
    "COURSE_CREATOR": [
        "courses.create",
        "earnings.manage_own",
    ],
    "CREATOR_REVIEWER": [
        "catalog.manage_topics",
        "courses.approve",
        "courses.publish",
        "courses.reject",
        "courses.set_pricing",
    ],
    "STAFF_WRITER": [
        "achievements.manage",
        "catalog.manage_categories",
        "courses.create",
        "earnings.manage_own",
        "mie.approve_topic_proposals",
    ],
    "STAFF_VERIFIER": [
        "catalog.manage_topics",
        "courses.approve",
        "courses.publish",
        "courses.reject",
        "courses.set_pricing",
    ],
    "STAFF_APPROVER": [
        "catalog.manage_topics",
        "catalog.view_topic_queue",
        "courses.approve",
        "courses.assign",
        "courses.edit",
        "courses.publish",
        "courses.reject",
        "courses.set_pricing",
        "courses.view",
    ],
    "AI_REVIEWER": [
    ],
    "QA_REVIEWER": [
        "courses.approve",
        "courses.reject",
    ],
    "ADMIN": [
        "achievements.manage",
        "audit.view",
        "catalog.manage_categories",
        "catalog.manage_topics",
        "catalog.view_topic_queue",
        "courses.approve",
        "courses.assign",
        "courses.decide_appeals",
        "courses.edit",
        "courses.manage_quality",
        "courses.publish",
        "courses.reject",
        "courses.set_pricing",
        "courses.view",
        "creators.approve_account",
        "creators.suspend",
        "creators.view_profile",
        "creators.view_wallet",
        "dashboard.view",
        "mie.view_pipeline",
        "platform.edit_settings",
        "reviewers.assign_track",
        "roles.view",
        "teams.suspend",
    ],
    "SUPER_ADMIN": [
        "achievements.manage",
        "audit.view",
        "catalog.manage_categories",
        "catalog.manage_topics",
        "catalog.view_topic_queue",
        "courses.approve",
        "courses.assign",
        "courses.create",
        "courses.decide_appeals",
        "courses.edit",
        "courses.manage_quality",
        "courses.publish",
        "courses.reject",
        "courses.set_pricing",
        "courses.view",
        "creators.approve_account",
        "creators.suspend",
        "creators.view_profile",
        "creators.view_wallet",
        "dashboard.view",
        "earnings.manage_own",
        "mie.approve_topic_proposals",
        "mie.manage_console",
        "mie.view_pipeline",
        "platform.edit_settings",
        "reviewers.assign_track",
        "roles.manage",
        "roles.view",
        "staff.add",
        "staff.full_access",
        "staff.view",
        "staff.view_detail",
        "teams.suspend",
    ],
}


def seed(apps, schema_editor):
    Role = apps.get_model("authorization", "Role")
    RolePermission = apps.get_model("authorization", "RolePermission")
    for key, label in ROLE_LABELS.items():
        role, _ = Role.objects.get_or_create(
            system_key=key, defaults={"name": label, "base_role": key}
        )
        RolePermission.objects.bulk_create(
            [RolePermission(role=role, codename=code) for code in SYSTEM_ROLE_GRANTS[key]],
            ignore_conflicts=True,
        )


def unseed(apps, schema_editor):
    Role = apps.get_model("authorization", "Role")
    Role.objects.filter(system_key__in=list(ROLE_LABELS)).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("authorization", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]

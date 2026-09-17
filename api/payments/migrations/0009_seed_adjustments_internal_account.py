"""Seed the internal ledger account that admin wallet adjustments post against."""

from django.db import migrations

CODE_NAME = "adjustments"


def seed(apps, schema_editor):
    apps.get_model("payments", "InternalAccount").objects.get_or_create(
        code_name=CODE_NAME, defaults={"name": "Admin Adjustments", "currency": "NGN"}
    )


def unseed(apps, schema_editor):
    apps.get_model("payments", "InternalAccount").objects.filter(
        code_name=CODE_NAME, balance=0
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("payments", "0008_auto_20260817_2142"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]

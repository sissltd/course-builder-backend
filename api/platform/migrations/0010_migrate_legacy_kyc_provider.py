from django.db import migrations


def migrate_legacy_kyc_provider(apps, schema_editor):
    PlatformSettings = apps.get_model("platform", "PlatformSettings")
    PlatformSettings.objects.filter(kyc_provider="YOUVERIFY").update(kyc_provider="SISSL")


def reverse_migrate_legacy_kyc_provider(apps, schema_editor):
    PlatformSettings = apps.get_model("platform", "PlatformSettings")
    PlatformSettings.objects.filter(kyc_provider="SISSL").update(kyc_provider="YOUVERIFY")


class Migration(migrations.Migration):
    dependencies = [
        ("platform", "0009_alter_platformsettings_kyc_provider"),
    ]

    operations = [
        migrations.RunPython(
            migrate_legacy_kyc_provider,
            reverse_migrate_legacy_kyc_provider,
        ),
    ]
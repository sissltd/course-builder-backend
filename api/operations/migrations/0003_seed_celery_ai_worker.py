from django.db import migrations


def seed_worker_service(apps, schema_editor):
    Service = apps.get_model("operations", "Service")
    Service.objects.get_or_create(
        name="Celery AI Worker",
        defaults={"priority": "HIGH", "display_order": 65},
    )


def unseed_worker_service(apps, schema_editor):
    apps.get_model("operations", "Service").objects.filter(
        name="Celery AI Worker"
    ).delete()


class Migration(migrations.Migration):
    dependencies = [("operations", "0002_seed_services_and_providers")]

    operations = [migrations.RunPython(seed_worker_service, unseed_worker_service)]

"""Seed the monitored services and generation providers.

Registry rows rather than a hardcoded list in code, so operations can add
or retire one without a deploy. Seeded here so the System Health and APE
Pipeline screens have their rows the moment the app is deployed, and
start showing real readings as soon as the probe runs - rather than
needing someone to populate them by hand first.

`get_or_create` keyed on name, so re-running is safe and an operator's
later edits (priority, is_active) are never overwritten.
"""

from django.db import migrations

# (name, priority, display_order)
SERVICES = [
    ("Creator Studio", "NORMAL", 10),
    ("API Gateway", "MEDIUM", 20),
    ("APE Pipeline", "NORMAL", 30),
    ("MIE Crawler", "NORMAL", 40),
    ("PostgreSQL", "HIGH", 50),
    ("Redis Cache", "HIGH", 60),
    ("S3/CDN", "NORMAL", 70),
    ("WellSaid TTS", "NORMAL", 80),
    ("Colossyan Video", "NORMAL", 90),
    ("Intron Sahara", "NORMAL", 100),
]

# (name, kind)
PROVIDERS = [
    ("WellSaid Labs", "VOICE"),
    ("Murf AI", "VOICE"),
    ("Google TTS", "FALLBACK"),
    ("Colossyan", "VIDEO"),
    ("Synthesia", "VIDEO"),
    ("HeyGen", "VIDEO"),
]


def seed(apps, schema_editor):
    Service = apps.get_model("operations", "Service")
    Provider = apps.get_model("operations", "Provider")

    for name, priority, order in SERVICES:
        Service.objects.get_or_create(
            name=name,
            defaults={"priority": priority, "display_order": order},
        )
    for name, kind in PROVIDERS:
        Provider.objects.get_or_create(name=name, defaults={"kind": kind})


def unseed(apps, schema_editor):
    """Remove only the rows this migration created, by name.

    Deliberately narrow: an operator may have registered services of their
    own, and a blanket delete would take those with it.
    """

    apps.get_model("operations", "Service").objects.filter(
        name__in=[name for name, _p, _o in SERVICES]
    ).delete()
    apps.get_model("operations", "Provider").objects.filter(
        name__in=[name for name, _k in PROVIDERS]
    ).delete()


class Migration(migrations.Migration):
    dependencies = [("operations", "0001_initial")]

    operations = [migrations.RunPython(seed, unseed)]

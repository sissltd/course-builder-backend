"""Give every existing developer an endpoint for the webhook_url they
registered, taking every event, and point their existing events at it.

That is exactly what they received before, so nothing changes for them on
deploy. Runs before 0011 drops DeveloperAccount.webhook_url. Idempotent: an
account that already has a live endpoint is left alone. The reverse copies
each account's earliest live endpoint URL back onto webhook_url.
"""

from django.db import migrations
from django.db.models import OuterRef, Subquery


def forward(apps, schema_editor):
    DeveloperAccount = apps.get_model("mie", "DeveloperAccount")
    WebhookEndpoint = apps.get_model("mie", "WebhookEndpoint")
    WebhookEvent = apps.get_model("mie", "WebhookEvent")

    covered = set(
        WebhookEndpoint.objects.filter(is_deleted=False).values_list(
            "developer_id", flat=True
        )
    )
    WebhookEndpoint.objects.bulk_create(
        [
            WebhookEndpoint(developer_id=account_id, url=url, all_events=True)
            for account_id, url in DeveloperAccount.objects.values_list(
                "id", "webhook_url"
            )
            if account_id not in covered and url
        ]
    )
    # One statement: each event takes its developer's earliest live endpoint.
    WebhookEvent.objects.filter(endpoint__isnull=True).update(
        endpoint_id=Subquery(
            WebhookEndpoint.objects.filter(
                developer__submissions__id=OuterRef("submission_id"),
                is_deleted=False,
            )
            .order_by("created_datetime")
            .values("id")[:1]
        )
    )


def backward(apps, schema_editor):
    DeveloperAccount = apps.get_model("mie", "DeveloperAccount")
    WebhookEndpoint = apps.get_model("mie", "WebhookEndpoint")
    DeveloperAccount.objects.update(
        webhook_url=Subquery(
            WebhookEndpoint.objects.filter(
                developer_id=OuterRef("pk"), is_deleted=False
            )
            .order_by("created_datetime")
            .values("url")[:1]
        )
    )


class Migration(migrations.Migration):
    dependencies = [
        ("mie", "0009_webhookendpoint"),
    ]

    operations = [
        migrations.RunPython(forward, backward),
    ]

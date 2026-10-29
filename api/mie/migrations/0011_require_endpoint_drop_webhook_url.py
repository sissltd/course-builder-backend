"""Every event now belongs to an endpoint, and the endpoint list replaces
DeveloperAccount.webhook_url (backfilled in 0010).

webhook_url is given an empty default before it is dropped so the reverse
can add the column back to a populated table; 0010's reverse then fills it.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("mie", "0010_backfill_webhook_endpoints"),
    ]

    operations = [
        migrations.AlterField(
            model_name="webhookevent",
            name="endpoint",
            field=models.ForeignKey(
                help_text="Endpoint this delivery goes to.",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="events",
                to="mie.webhookendpoint",
                verbose_name="Endpoint",
            ),
        ),
        migrations.AlterField(
            model_name="developeraccount",
            name="webhook_url",
            field=models.URLField(
                blank=True,
                default="",
                help_text=(
                    "HTTPS endpoint that receives a signed POST for every "
                    "event against this developer's submissions."
                ),
                verbose_name="Webhook URL",
            ),
        ),
        migrations.RemoveField(
            model_name="developeraccount",
            name="webhook_url",
        ),
    ]

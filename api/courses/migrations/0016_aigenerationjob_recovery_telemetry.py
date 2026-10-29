from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("courses", "0015_normalize_assessment_options")]

    operations = [
        migrations.AddField(
            model_name="aigenerationjob",
            name="dispatch_attempts",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="aigenerationjob",
            name="last_dispatch_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="aigenerationjob",
            name="last_heartbeat_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="aigenerationjob",
            name="retry_count",
            field=models.PositiveSmallIntegerField(default=0),
        ),
    ]

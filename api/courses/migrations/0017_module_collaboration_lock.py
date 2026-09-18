from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("courses", "0016_aigenerationjob_recovery_telemetry")]

    operations = [
        migrations.AddField(
            model_name="module",
            name="collaboration_locked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="module",
            name="collaboration_locked_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name="+",
                to="users.user",
            ),
        ),
    ]

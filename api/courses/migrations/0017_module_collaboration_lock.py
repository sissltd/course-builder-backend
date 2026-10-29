from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("courses", "0016_aigenerationjob_recovery_telemetry")]

    operations = [
        migrations.AddField(
            model_name="module",
            name="collaboration_locked_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When collaborator editing was frozen.",
                null=True,
                verbose_name="Collaboration Locked At",
            ),
        ),
        migrations.AddField(
            model_name="module",
            name="collaboration_locked_by",
            field=models.ForeignKey(
                blank=True,
                help_text="Course creator who has frozen collaborator editing.",
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name="+",
                to="users.user",
                verbose_name="Collaboration Locked By",
            ),
        ),
    ]

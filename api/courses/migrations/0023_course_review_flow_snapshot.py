from django.db import migrations, models


def preserve_existing_flows(apps, schema_editor):
    Course = apps.get_model("courses", "Course")
    Settings = apps.get_model("platform", "PlatformSettings")
    alias = schema_editor.connection.alias
    enabled = (
        Settings.objects.using(alias)
        .values_list("staged_review_flow_enabled", flat=True)
        .first()
    )
    Course.objects.using(alias).filter(staged_review_flow__isnull=True).update(
        staged_review_flow=bool(enabled)
    )


class Migration(migrations.Migration):
    dependencies = [
        ("courses", "0022_lesson_video_transcript"),
        ("platform", "0022_remove_auto_credit_delay"),
    ]
    operations = [
        migrations.AddField(
            model_name="course",
            name="staged_review_flow",
            field=models.BooleanField(
                null=True,
                default=None,
                editable=False,
                help_text="Review mode frozen at first submission. Existing courses retain their migrated mode.",
            ),
        ),
        migrations.RunPython(preserve_existing_flows, migrations.RunPython.noop),
    ]

import uuid

from django.db import migrations, models


def reserve_existing_approved(apps, schema_editor):
    Submission = apps.get_model("mie", "CourseSubmission")
    for row in (
        Submission.objects.using(schema_editor.connection.alias)
        .filter(models.Q(status="APPROVED") | models.Q(resulting_course__isnull=False))
        .iterator()
    ):
        row.reserved_course_id = row.resulting_course_id or uuid.uuid4()
        row.save(
            using=schema_editor.connection.alias, update_fields=["reserved_course_id"]
        )


class Migration(migrations.Migration):
    dependencies = [("mie", "0011_require_endpoint_drop_webhook_url")]
    operations = [
        migrations.AddField(
            model_name="coursesubmission",
            name="reserved_course_id",
            field=models.UUIDField(
                null=True,
                blank=True,
                unique=True,
                editable=False,
                help_text="Stable course identity issued on topic approval; no course content or terms are created by this reservation.",
            ),
        ),
        migrations.RunPython(reserve_existing_approved, migrations.RunPython.noop),
    ]

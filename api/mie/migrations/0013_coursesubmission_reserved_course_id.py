import uuid

from django.db import migrations, models


def reserve_existing_course_ids(apps, schema_editor):
    """Give every idea that was approved, or already has a course, the id it
    would have been given at approval: its course's id when one exists,
    otherwise a fresh one."""

    CourseSubmission = apps.get_model("mie", "CourseSubmission")
    pending = CourseSubmission.objects.filter(reserved_course_id__isnull=True).filter(
        models.Q(status="APPROVED") | models.Q(resulting_course__isnull=False)
    )
    for submission in pending.iterator():
        submission.reserved_course_id = submission.resulting_course_id or uuid.uuid4()
        submission.save(update_fields=["reserved_course_id"])


class Migration(migrations.Migration):
    dependencies = [
        ("mie", "0012_video_assignment_and_idempotency_keys"),
    ]

    operations = [
        migrations.AddField(
            model_name="coursesubmission",
            name="reserved_course_id",
            field=models.UUIDField(
                blank=True,
                editable=False,
                help_text="The course id fixed at the idea's first approval and sent to the developer. The first course push creates the course under it, so the id never changes. Empty until the idea is approved.",
                null=True,
                unique=True,
                verbose_name="Reserved Course ID",
            ),
        ),
        migrations.RunPython(reserve_existing_course_ids, migrations.RunPython.noop),
    ]

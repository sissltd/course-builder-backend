from django.db import migrations


def seed_default_course_version(apps, schema_editor):
    CourseVersion = apps.get_model("courses", "CourseVersion")
    CourseVersion.objects.get_or_create(
        label="1.0",
        defaults={"is_active": True},
    )


class Migration(migrations.Migration):

    dependencies = [
        ("courses", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_default_course_version, migrations.RunPython.noop),
    ]
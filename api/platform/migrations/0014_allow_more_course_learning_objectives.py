from django.db import migrations, models


def widen_existing_default(apps, schema_editor):
    platform_settings = apps.get_model("platform", "PlatformSettings")
    platform_settings.objects.filter(
        course_learning_objectives_min=5,
        course_learning_objectives_max=5,
    ).update(course_learning_objectives_max=10)


class Migration(migrations.Migration):

    dependencies = [
        ("platform", "0013_alter_platformsettings_payment_processor"),
    ]

    operations = [
        migrations.AlterField(
            model_name="platformsettings",
            name="course_learning_objectives_max",
            field=models.PositiveIntegerField(
                default=10,
                verbose_name="Course Learning Objectives Max",
            ),
        ),
        migrations.RunPython(widen_existing_default, migrations.RunPython.noop),
    ]

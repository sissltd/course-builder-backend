from django.db import migrations, models


def align_course_objective_minimum(apps, schema_editor):
    PlatformSettings = apps.get_model("platform", "PlatformSettings")
    PlatformSettings.objects.filter(course_learning_objectives_min=2).update(
        course_learning_objectives_min=5
    )


class Migration(migrations.Migration):
    dependencies = [("platform", "0004_platformsettings_payment_processor")]

    operations = [
        migrations.AlterField(
            model_name="platformsettings",
            name="course_learning_objectives_min",
            field=models.PositiveIntegerField(
                default=5, verbose_name="Course Learning Objectives Min"
            ),
        ),
        migrations.AddField(
            model_name="platformsettings",
            name="lesson_learning_objectives_min",
            field=models.PositiveIntegerField(
                default=2, verbose_name="Lesson Learning Objectives Min"
            ),
        ),
        migrations.AddField(
            model_name="platformsettings",
            name="lesson_learning_objectives_max",
            field=models.PositiveIntegerField(
                default=5, verbose_name="Lesson Learning Objectives Max"
            ),
        ),
        migrations.RunPython(align_course_objective_minimum, migrations.RunPython.noop),
    ]

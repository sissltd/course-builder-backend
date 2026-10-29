from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("platform", "0005_separate_course_and_lesson_objective_limits")]

    operations = [
        migrations.RemoveField(
            model_name="platformsettings",
            name="lesson_quiz_questions_min",
        ),
        migrations.RemoveField(
            model_name="platformsettings",
            name="lesson_quiz_questions_max",
        ),
    ]

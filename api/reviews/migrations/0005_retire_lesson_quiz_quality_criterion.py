from django.db import migrations


def retire_lesson_quiz_criterion(apps, schema_editor):
    QualityCheckCriterion = apps.get_model("reviews", "QualityCheckCriterion")
    QualityCheckCriterion.objects.filter(
        section="Assessments",
        label="Lesson quizzes",
    ).update(is_active=False)


def restore_lesson_quiz_criterion(apps, schema_editor):
    QualityCheckCriterion = apps.get_model("reviews", "QualityCheckCriterion")
    QualityCheckCriterion.objects.filter(
        section="Assessments",
        label="Lesson quizzes",
    ).update(is_active=True)


class Migration(migrations.Migration):
    dependencies = [
        ("reviews", "0004_reviewaction_stage_qualitycheckrun_reviewcomment_and_more"),
    ]

    operations = [
        migrations.RunPython(
            retire_lesson_quiz_criterion,
            reverse_code=restore_lesson_quiz_criterion,
        ),
    ]

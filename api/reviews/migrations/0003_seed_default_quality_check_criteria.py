from django.db import migrations


def seed_default_criteria(apps, schema_editor):
    """Seed the quality-check checklist template with the default criteria.

    Idempotent by section+label so re-running (or admin edits made between
    environments) never duplicates rows.
    """

    QualityCheckCriterion = apps.get_model("reviews", "QualityCheckCriterion")
    defaults = [
        ("Course information", "Course title"),
        ("Course information", "Course description"),
        ("Course information", "Learning objectives"),
        ("Course information", "Preview video"),
        ("Course Outline", "Module count"),
        ("Course Outline", "Lessons per module"),
        ("Course Modules", "Lesson scripts"),
        ("Course Modules", "Lesson requirements"),
        ("Version", "Version selected"),
        ("Thumbnail", "Thumbnail set"),
        ("Assessments", "Final assessment"),
        ("Assessments", "Lesson quizzes"),
    ]
    for section, label in defaults:
        QualityCheckCriterion.objects.get_or_create(section=section, label=label)


def unseed(apps, schema_editor):
    QualityCheckCriterion = apps.get_model("reviews", "QualityCheckCriterion")
    QualityCheckCriterion.objects.filter(
        section__in={
            "Course information",
            "Course Outline",
            "Course Modules",
            "Version",
            "Thumbnail",
            "Assessments",
        }
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("reviews", "0002_qualitycheckcriterion_coursequalitycheck_reviewflag"),
    ]

    operations = [
        migrations.RunPython(seed_default_criteria, reverse_code=unseed),
    ]

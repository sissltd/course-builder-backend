from django.db import migrations


def normalize_assessment_options(apps, schema_editor):
    Assessment = apps.get_model("courses", "Assessment")

    for assessment in Assessment.objects.iterator():
        questions = assessment.questions or []
        changed = False
        normalized_questions = []
        for question in questions:
            normalized_question = dict(question)
            options = question.get("options")
            if isinstance(options, list):
                normalized_options = [
                    option.get("text") if isinstance(option, dict) else option
                    for option in options
                ]
                if normalized_options != options:
                    normalized_question["options"] = normalized_options
                    changed = True
            normalized_questions.append(normalized_question)

        if changed:
            assessment.questions = normalized_questions
            assessment.save(update_fields=["questions", "updated_datetime"])


class Migration(migrations.Migration):
    dependencies = [("courses", "0014_start_in_flight_courses_at_first_review")]

    operations = [
        migrations.RunPython(
            normalize_assessment_options,
            migrations.RunPython.noop,
        ),
    ]

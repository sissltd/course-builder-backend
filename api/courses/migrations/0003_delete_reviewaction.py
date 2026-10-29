# Second half of the ReviewAction relocation: drop the model from
# api.courses' state only - the table itself was renamed (not dropped) by
# api.reviews.0001, preserving all audit rows.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("courses", "0002_seed_default_course_version"),
        ("reviews", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(
                    name="ReviewAction",
                ),
            ],
        ),
    ]

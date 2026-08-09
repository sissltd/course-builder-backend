"""Release Category from the courses app.

Step 2 of 3 in moving Category out of `courses` without touching its data.

Also state-only. `categories.0001_initial` has already adopted the model, so
this migration hands it over: the Course.category and
CreatorProfile.primary_expertise_category foreign keys are repointed at
`categories.Category`, and `courses` drops the model from its state.

Nothing runs against the database here, deliberately:

* A real DeleteModel would DROP the table and every category in it.
* The foreign keys need no DDL — the column and its constraint already point
  at the same physical table, which is not renamed until 0003. Postgres
  carries foreign key constraints across a table rename automatically, so the
  constraints stay valid throughout.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("courses", "0001_initial"),
        ("categories", "0001_initial"),
        ("onboarding", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterField(
                    model_name="course",
                    name="category",
                    field=models.ForeignKey(
                        help_text="Category this course belongs to.",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="courses",
                        to="categories.category",
                        verbose_name="Category",
                    ),
                ),
                migrations.RemoveIndex(
                    model_name="category",
                    name="category_status_track_idx",
                ),
                migrations.DeleteModel(name="Category"),
            ],
        ),
    ]

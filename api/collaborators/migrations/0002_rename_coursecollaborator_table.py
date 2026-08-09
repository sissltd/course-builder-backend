"""Rename the physical table from courses_coursecollaborator to
collaborators_coursecollaborator.

Step 3 of 3 in moving CourseCollaborator out of `courses`.

Now that `courses` has released the model (courses.0007) this is safe to run
for real. Setting db_table back to None restores Django's default naming,
`<app_label>_<model>`, so the model no longer needs the db_table override that
0001 used to keep state and database in sync mid-move.

This is the only step that touches the database, and it is a rename rather
than a copy: no rows move, and Postgres carries the primary key, indexes, and
the foreign key constraints from courses_course and the user model across the
rename automatically.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("collaborators", "0001_initial"),
        # Must run after courses drops CourseCollaborator from its state,
        # otherwise two apps would briefly claim the same table.
        ("courses", "0007_move_coursecollaborator_to_collaborators_app"),
    ]

    operations = [
        migrations.AlterModelTable(
            name="coursecollaborator",
            table=None,
        ),
    ]

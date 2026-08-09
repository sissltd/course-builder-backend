"""Release CourseCollaborator from the courses app.

Step 1 of 3 in moving CourseCollaborator out of `courses` into its own
`collaborators` app, following the same pattern used for Category
(see courses.0002_move_category_to_categories_app).

This is state-only, deliberately:

* A real DeleteModel would DROP the table and every collaborator row in it.
* `collaborators.0001_initial` adopts the model with `db_table` pinned to its
  current physical name, so Django's migration state matches the database
  exactly during the handover. The physical table rename happens in
  `collaborators.0002`, once `courses` has released the model here.

Nothing else in `courses` referenced CourseCollaborator via a foreign key -
it's the one holding FKs to `courses.Course` and `users.User`, not the other
way around - so no other field needs repointing.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("courses", "0006_topic_reserved_by_topic_reserved_until_and_more"),
        ("collaborators", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.DeleteModel(name="CourseCollaborator"),
            ],
        ),
    ]

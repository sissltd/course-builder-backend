"""Adopt the existing CourseCollaborator model into the collaborators app.

Step 2 of 3 in moving CourseCollaborator out of `courses` without touching
its data (see collaborators.0002 and courses.0007_move_coursecollaborator_to_collaborators_app).

This is a STATE-ONLY migration: `database_operations` is empty because the
table already exists (created by courses.0003_coursecollaborator) and still
holds live rows. Running a plain CreateModel here would try to build a second
table and fail; running a plain DeleteModel in `courses` would DROP the real
one. So the model is re-declared here with `db_table` pinned to its current
physical name, which makes Django's migration state match the database
exactly.

The physical rename to `collaborators_coursecollaborator` happens in 0002,
after `courses` has released the model in its own state migration.
"""

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        # The table this migration adopts is created by courses.0003, so that
        # must have run first.
        ("courses", "0003_coursecollaborator"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.CreateModel(
                    name="CourseCollaborator",
                    fields=[
                        (
                            "id",
                            models.UUIDField(
                                default=uuid.uuid4,
                                editable=False,
                                primary_key=True,
                                serialize=False,
                            ),
                        ),
                        (
                            "created_datetime",
                            models.DateTimeField(
                                auto_now_add=True,
                                help_text="datetime of object creation",
                                verbose_name="Created datetime",
                            ),
                        ),
                        (
                            "updated_datetime",
                            models.DateTimeField(
                                auto_now=True,
                                help_text="datetime of object update",
                                verbose_name="Updated datetime",
                            ),
                        ),
                        (
                            "role",
                            models.CharField(
                                choices=[
                                    ("COLLABORATOR", "Collaborator"),
                                    ("ADMIN", "Admin"),
                                ],
                                default="COLLABORATOR",
                                help_text="This collaborator's access level on the course.",
                                max_length=15,
                                verbose_name="Role",
                            ),
                        ),
                        (
                            "course",
                            models.ForeignKey(
                                help_text="Course this collaborator has access to.",
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="collaborators",
                                to="courses.course",
                                verbose_name="Course",
                            ),
                        ),
                        (
                            "invited_by",
                            models.ForeignKey(
                                blank=True,
                                help_text="User who invited this collaborator.",
                                null=True,
                                on_delete=django.db.models.deletion.SET_NULL,
                                related_name="+",
                                to=settings.AUTH_USER_MODEL,
                                verbose_name="Invited By",
                            ),
                        ),
                        (
                            "user",
                            models.ForeignKey(
                                help_text="The collaborating user.",
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="course_collaborations",
                                to=settings.AUTH_USER_MODEL,
                                verbose_name="User",
                            ),
                        ),
                    ],
                    options={
                        "verbose_name": "Course Collaborator",
                        "verbose_name_plural": "Course Collaborators",
                        "ordering": ["-created_datetime"],
                        "db_table": "courses_coursecollaborator",
                        "constraints": [
                            models.UniqueConstraint(
                                fields=("course", "user"),
                                name="unique_collaborator_per_course",
                            )
                        ],
                    },
                ),
            ],
        ),
    ]

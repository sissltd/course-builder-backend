# First half of the ReviewAction relocation from api.courses to api.reviews.
# The model moves apps but keeps its data: the existing courses_reviewaction
# table is renamed in-place (database operation) while Django's state records
# a fresh CreateModel for the new app location. No audit rows are lost.

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("courses", "0002_seed_default_course_version"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="ReviewAction",
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
                            "action",
                            models.CharField(
                                choices=[
                                    ("APPROVE", "Approve"),
                                    ("REJECT", "Reject"),
                                ],
                                help_text="The decision made by the reviewer.",
                                max_length=10,
                                verbose_name="Action",
                            ),
                        ),
                        (
                            "feedback",
                            models.JSONField(
                                blank=True,
                                default=dict,
                                help_text="Structured reviewer feedback.",
                                verbose_name="Feedback",
                            ),
                        ),
                        (
                            "course",
                            models.ForeignKey(
                                help_text="Course this review action was taken on.",
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="review_actions",
                                to="courses.course",
                                verbose_name="Course",
                            ),
                        ),
                        (
                            "reviewer",
                            models.ForeignKey(
                                help_text="Reviewer who took this action.",
                                null=True,
                                on_delete=django.db.models.deletion.SET_NULL,
                                related_name="review_actions",
                                to=settings.AUTH_USER_MODEL,
                                verbose_name="Reviewer",
                            ),
                        ),
                    ],
                    options={
                        "verbose_name": "Review Action",
                        "verbose_name_plural": "Review Actions",
                        "ordering": ["-created_datetime"],
                        "indexes": [
                            models.Index(
                                fields=["course", "-created_datetime"],
                                name="reviewaction_course_dt_idx",
                            )
                        ],
                    },
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql="ALTER TABLE courses_reviewaction RENAME TO reviews_reviewaction;",
                    reverse_sql="ALTER TABLE reviews_reviewaction RENAME TO courses_reviewaction;",
                ),
            ],
        ),
    ]

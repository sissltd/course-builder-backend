"""Adopt the existing Category model into the categories app.

Step 1 of 3 in moving Category out of `courses` without touching its data.

This is a STATE-ONLY migration: `database_operations` is empty because the
table already exists (created by courses.0001_initial) and still holds live
rows. Running a plain CreateModel here would try to build a second table and
fail; running a plain DeleteModel in `courses` would DROP the real one. So the
model is re-declared here with `db_table` pinned to its current physical name,
which makes Django's migration state match the database exactly.

The physical rename to `categories_category` happens in 0002, after `courses`
has released the model in its own state migration.
"""

import uuid
from decimal import Decimal

import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        # The table this migration adopts is created by courses.0001_initial,
        # so that must have run first.
        ("courses", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.CreateModel(
                    name="Category",
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
                            "name",
                            models.CharField(
                                help_text="Category display name.",
                                max_length=150,
                                unique=True,
                                verbose_name="Name",
                            ),
                        ),
                        (
                            "description",
                            models.TextField(
                                blank=True,
                                default="",
                                help_text=(
                                    "Description of the category shown to creators."
                                ),
                                verbose_name="Description",
                            ),
                        ),
                        (
                            "creator_price",
                            models.DecimalField(
                                decimal_places=2,
                                help_text=(
                                    "Fixed price paid to a creator for an "
                                    "approved course in this category."
                                ),
                                max_digits=10,
                                validators=[
                                    django.core.validators.MinValueValidator(
                                        Decimal("0")
                                    )
                                ],
                                verbose_name="Creator Price",
                            ),
                        ),
                        (
                            "track_preference",
                            models.CharField(
                                choices=[
                                    ("CREATOR_PREFERRED", "Creator Preferred"),
                                    ("AI_PREFERRED", "AI Preferred"),
                                    ("OPEN", "Open"),
                                ],
                                default="OPEN",
                                help_text=(
                                    "Which production track this category is "
                                    "best suited for."
                                ),
                                max_length=20,
                                verbose_name="Track Preference",
                            ),
                        ),
                        (
                            "status",
                            models.CharField(
                                choices=[
                                    ("ACTIVE", "Active"),
                                    ("INACTIVE", "Inactive"),
                                ],
                                default="ACTIVE",
                                help_text=(
                                    "Whether the category currently accepts "
                                    "new course submissions."
                                ),
                                max_length=10,
                                verbose_name="Status",
                            ),
                        ),
                        (
                            "created_by",
                            models.ForeignKey(
                                blank=True,
                                help_text="User who created the object",
                                null=True,
                                on_delete=django.db.models.deletion.SET_NULL,
                                related_name="%(app_label)s_%(class)s_created_by",
                                to=settings.AUTH_USER_MODEL,
                                verbose_name="Created by",
                            ),
                        ),
                        (
                            "updated_by",
                            models.ForeignKey(
                                blank=True,
                                help_text="User who updated the object",
                                null=True,
                                on_delete=django.db.models.deletion.SET_NULL,
                                related_name="%(app_label)s_%(class)s_updated_by",
                                to=settings.AUTH_USER_MODEL,
                                verbose_name="Updated by",
                            ),
                        ),
                    ],
                    options={
                        "verbose_name": "Category",
                        "verbose_name_plural": "Categories",
                        "ordering": ["name"],
                        # Pinned to the pre-move table name so state matches the
                        # database. 0002 renames it for real.
                        "db_table": "courses_category",
                    },
                ),
                # Declared in state only, like the model: courses.0001_initial
                # already built this index on the physical table, and it
                # survives the rename in 0002.
                migrations.AddIndex(
                    model_name="category",
                    index=models.Index(
                        fields=["status", "track_preference"],
                        name="category_status_track_idx",
                    ),
                ),
            ],
        ),
    ]

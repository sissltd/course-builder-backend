"""Repoint CreatorProfile.primary_expertise_category at the categories app.

State-only, for the same reason as courses.0002: the column and its foreign
key constraint already reference the same physical table, which is renamed in
categories.0002 with the constraint carried across automatically.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("onboarding", "0001_initial"),
        ("courses", "0002_move_category_to_categories_app"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterField(
                    model_name="creatorprofile",
                    name="primary_expertise_category",
                    field=models.ForeignKey(
                        blank=True,
                        help_text=(
                            "Primary area-of-expertise category selected "
                            "during onboarding."
                        ),
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="categories.category",
                        verbose_name="Primary Expertise Category",
                    ),
                ),
            ],
        ),
    ]

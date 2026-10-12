"""Creator prices by how a course is made, not by its difficulty.

A category's three level prices (Beginner / Intermediate / Advanced) become
three price types: without video, with video, AI-built. Each new price
starts at the category's Beginner rate (its entry rate), so nobody is paid
more than before until a Writer sets them. A topic's single price becomes
an override of all three types, so a topic priced today still pays what it
did; a Writer can then clear an override to fall back to the category.
"""

from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import migrations, models

PRICE = {"max_digits": 10, "decimal_places": 2, "validators": [MinValueValidator(Decimal(0))]}
TYPES = ("without_video", "with_video", "ai_built")
LEVELS = ("beginner", "intermediate", "advanced")


def to_price_types(apps, schema_editor):
    Category = apps.get_model("catalog", "Category")
    Topic = apps.get_model("catalog", "Topic")
    categories = list(Category.objects.all())
    for category in categories:
        for kind in TYPES:
            setattr(category, f"creator_price_{kind}", category.creator_price_beginner)
    Category.objects.bulk_update(categories, [f"creator_price_{kind}" for kind in TYPES], batch_size=500)
    topics = list(Topic.objects.all())
    for topic in topics:
        for kind in TYPES:
            setattr(topic, f"creator_price_{kind}", topic.creator_price)
    Topic.objects.bulk_update(topics, [f"creator_price_{kind}" for kind in TYPES], batch_size=500)


def to_price_levels(apps, schema_editor):
    """Reverse: every level takes the with-video price; a topic takes its
    own with-video price, or its category's."""

    Category = apps.get_model("catalog", "Category")
    Topic = apps.get_model("catalog", "Topic")
    categories = list(Category.objects.all())
    for category in categories:
        for level in LEVELS:
            setattr(category, f"creator_price_{level}", category.creator_price_with_video)
    Category.objects.bulk_update(categories, [f"creator_price_{level}" for level in LEVELS], batch_size=500)
    topics = list(Topic.objects.select_related("category"))
    for topic in topics:
        topic.creator_price = (
            topic.creator_price_with_video
            if topic.creator_price_with_video is not None
            else topic.category.creator_price_with_video
        )
    Topic.objects.bulk_update(topics, ["creator_price"], batch_size=500)


class Migration(migrations.Migration):
    dependencies = [("catalog", "0008_alter_categoryrequest_description")]

    operations = [
        *[
            migrations.AddField("category", f"creator_price_{kind}", models.DecimalField(null=True, **PRICE))
            for kind in TYPES
        ],
        *[
            migrations.AddField(
                "topic",
                f"creator_price_{kind}",
                models.DecimalField(
                    verbose_name=f"Creator Price - {label}",
                    help_text=f"This topic's own {kind_text} price. Blank uses the category's.",
                    null=True,
                    blank=True,
                    **PRICE,
                ),
            )
            for kind, label, kind_text in zip(
                TYPES, ("Without Video", "With Video", "AI-built"), ("without-video", "with-video", "AI-built"), strict=True
            )
        ],
        *[
            migrations.AlterField("category", f"creator_price_{level}", models.DecimalField(null=True, **PRICE))
            for level in LEVELS
        ],
        migrations.AlterField("topic", "creator_price", models.DecimalField(null=True, **PRICE)),
        migrations.RunPython(to_price_types, to_price_levels),
        *[migrations.RemoveField("category", f"creator_price_{level}") for level in LEVELS],
        migrations.RemoveField("topic", "creator_price"),
        migrations.AlterField(
            "category",
            "creator_price_without_video",
            models.DecimalField(
                verbose_name="Creator Price - Without Video",
                help_text="Paid to a creator who writes the course and leaves its video to the Production Engine. Paid when Second Review approves it.",
                **PRICE,
            ),
        ),
        migrations.AlterField(
            "category",
            "creator_price_with_video",
            models.DecimalField(
                verbose_name="Creator Price - With Video",
                help_text="Paid to a creator who writes the course and adds its video, and to a paid developer. Paid when QA approves it.",
                **PRICE,
            ),
        ),
        migrations.AlterField(
            "category",
            "creator_price_ai_built",
            models.DecimalField(
                verbose_name="Creator Price - AI-built",
                help_text="Paid to a creator who builds the course with our AI. Paid at Second Review or QA, by who supplies the video.",
                **PRICE,
            ),
        ),
    ]

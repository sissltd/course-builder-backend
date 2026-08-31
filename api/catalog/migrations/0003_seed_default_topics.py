import uuid

from django.db import migrations
from django.utils.text import slugify


SEED_TOPICS = [
    (
        "Web Development",
        ["HTML & CSS Fundamentals", "JavaScript Development", "React Development"],
    ),
    (
        "Data Science & Analytics",
        ["Python for Data Analysis", "Advanced Excel", "Power BI"],
    ),
    (
        "Mobile App Development",
        [
            "Flutter Development",
            "React Native Development",
            "Android Development with Kotlin",
        ],
    ),
    (
        "UI/UX Design",
        ["UI/UX Design Fundamentals", "Figma for Interface Design", "UX Research"],
    ),
    (
        "Cloud & DevOps",
        [
            "AWS Cloud Practitioner",
            "Microsoft Azure Fundamentals",
            "Docker & Kubernetes",
        ],
    ),
    (
        "Cybersecurity",
        ["Cybersecurity Fundamentals", "Ethical Hacking", "Network Security"],
    ),
    (
        "Digital Marketing",
        [
            "Digital Marketing Fundamentals",
            "Search Engine Optimization",
            "Social Media Marketing",
        ],
    ),
    (
        "Product Management",
        ["Project Management", "Agile & Scrum", "Product Strategy"],
    ),
    (
        "Blockchain & Web3",
        [
            "Blockchain Fundamentals",
            "Solidity Smart Contract Development",
            "Web3 Application Development",
        ],
    ),
    (
        "Business & Entrepreneurship",
        ["Entrepreneurship Fundamentals", "Business Strategy", "Financial Management"],
    ),
]


def topic_id(category_name, topic_name):
    """Return a stable ID so rollback can distinguish rows this seed created."""

    return uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"soludesks:catalog-topic:{category_name}:{topic_name}",
    )


def seed_default_topics(apps, schema_editor):
    Category = apps.get_model("catalog", "Category")
    Topic = apps.get_model("catalog", "Topic")

    for category_name, topic_names in SEED_TOPICS:
        category = Category.objects.get(name=category_name)
        for topic_name in topic_names:
            Topic.objects.get_or_create(
                category=category,
                name=topic_name,
                defaults={
                    "id": topic_id(category_name, topic_name),
                    "slug": slugify(topic_name)[:160],
                    "creator_price": category.creator_price,
                    "status": "ACTIVE",
                },
            )


def remove_seeded_topics(apps, schema_editor):
    Topic = apps.get_model("catalog", "Topic")
    seeded_ids = [
        topic_id(category_name, topic_name)
        for category_name, topic_names in SEED_TOPICS
        for topic_name in topic_names
    ]
    Topic.objects.filter(id__in=seeded_ids).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0002_seed_default_categories"),
    ]

    operations = [
        migrations.RunPython(seed_default_topics, remove_seeded_topics),
    ]

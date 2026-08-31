"""Shared test-suite helpers."""

import uuid

from django.db import connection
from django.utils.text import slugify


SEED_CATEGORIES = [
    (
        "Web Development",
        "Frontend, backend, and full-stack engineering courses.",
        "150000.00",
        "CREATOR_PREFERRED",
    ),
    (
        "Data Science & Analytics",
        "Data analysis, machine learning, and visualisation courses.",
        "180000.00",
        "CREATOR_PREFERRED",
    ),
    (
        "Mobile App Development",
        "iOS, Android, and cross-platform app development courses.",
        "160000.00",
        "CREATOR_PREFERRED",
    ),
    (
        "UI/UX Design",
        "Interface design, design systems, and user research courses.",
        "120000.00",
        "CREATOR_PREFERRED",
    ),
    (
        "Cloud & DevOps",
        "Cloud infrastructure, CI/CD, and site-reliability courses.",
        "200000.00",
        "CREATOR_PREFERRED",
    ),
    (
        "Cybersecurity",
        "Security engineering, offensive security, and compliance courses.",
        "190000.00",
        "CREATOR_PREFERRED",
    ),
    (
        "Digital Marketing",
        "Growth, SEO, paid media, and content strategy courses.",
        "90000.00",
        "CREATOR_PREFERRED",
    ),
    (
        "Product Management",
        "Product discovery, delivery, and analytics courses.",
        "140000.00",
        "CREATOR_PREFERRED",
    ),
    (
        "Blockchain & Web3",
        "Smart contracts, protocols, and decentralised app courses.",
        "210000.00",
        "OPEN",
    ),
    (
        "Business & Entrepreneurship",
        "Startup operations, finance, and leadership courses.",
        "100000.00",
        "OPEN",
    ),
]

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
    ("Product Management", ["Project Management", "Agile & Scrum", "Product Strategy"]),
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


def reseed_reference_data() -> None:
    """Re-apply migration-seeded rows wiped by a TransactionTestCase flush.

    TransactionTestCase tears down by flushing every table - including rows
    inserted by data migrations (quality-check criteria, internal ledger
    accounts, course versions). Any app tested after such a case would find
    its seed data missing. TransactionTestCase subclasses call this at the
    end of _fixture_teardown so the database returns to its post-migration
    state. Keep this in sync with new seed migrations.
    """

    from api.catalog.models import Category, Topic
    from api.courses.models import CourseVersion
    from api.payments.models.ledgeraccount_models import InternalAccount
    from api.reviews.models import QualityCheckCriterion

    categories = {}
    for name, description, price, track in SEED_CATEGORIES:
        category, _ = Category.objects.get_or_create(
            name=name,
            defaults={
                "slug": slugify(name)[:160],
                "description": description,
                "creator_price": price,
                "track_preference": track,
            },
        )
        categories[name] = category

    for category_name, topic_names in SEED_TOPICS:
        category = categories[category_name]
        for topic_name in topic_names:
            Topic.objects.get_or_create(
                category=category,
                name=topic_name,
                defaults={
                    "id": uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"soludesks:catalog-topic:{category_name}:{topic_name}",
                    ),
                    "slug": slugify(topic_name)[:160],
                    "creator_price": category.creator_price,
                    "status": "ACTIVE",
                },
            )

    CourseVersion.objects.get_or_create(label="1.0", defaults={"is_active": True})
    for code_name, name in (
        ("paystack_transfer", "Paystack Transfer"),
        ("general", "General Ledger"),
        ("suspense", "Suspense Ledger"),
    ):
        InternalAccount.objects.get_or_create(
            code_name=code_name,
            defaults={"name": name, "currency": "NGN"},
        )
    criteria = [
        ("Course information", "Course title"),
        ("Course information", "Course description"),
        ("Course information", "Learning objectives"),
        ("Course information", "Preview video"),
        ("Course Outline", "Module count"),
        ("Course Outline", "Lessons per module"),
        ("Course Modules", "Lesson scripts"),
        ("Course Modules", "Lesson requirements"),
        ("Version", "Version selected"),
        ("Thumbnail", "Thumbnail set"),
        ("Assessments", "Final assessment"),
        ("Assessments", "Lesson quizzes"),
    ]
    for section, label in criteria:
        QualityCheckCriterion.objects.get_or_create(
            section=section, label=label, defaults={"order_index": 0}
        )


def transaction_teardown_with_reseed(test_case) -> None:
    """Run TransactionTestCase._fixture_teardown, then restore seed rows.

    Skipped on the mirror databases used by parallel runners - each mirror
    needs its own pass, handled by the runner itself.
    """

    test_case._fixture_teardown_original()
    if connection.alias in test_case.databases:
        reseed_reference_data()

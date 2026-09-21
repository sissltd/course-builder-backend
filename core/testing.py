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

    categories = {}
    # Category no longer carries a description; the seed tuples keep theirs
    # as documentation of what each category covers.
    for name, _description, price, track in SEED_CATEGORIES:
        category, _ = Category.objects.get_or_create(
            name=name,
            defaults={
                "slug": slugify(name)[:160],
                # Seed every tier at the same rate; differentiating them is
                # a deliberate admin edit, not a fixture concern.
                "creator_price_beginner": price,
                "creator_price_intermediate": price,
                "creator_price_advanced": price,
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
                    "creator_price": category.creator_price_beginner,
                    "status": "ACTIVE",
                },
            )

    CourseVersion.objects.get_or_create(label="1.0", defaults={"is_active": True})
    for code_name, name in (
        ("paystack_transfer", "Paystack Transfer"),
        ("general", "General Ledger"),
        ("suspense", "Suspense Ledger"),
        ("flutterwave_transfer", "Flutterwave Transfer"),
        ("adjustments", "Admin Adjustments"),
    ):
        InternalAccount.objects.get_or_create(
            code_name=code_name,
            defaults={"name": name, "currency": "NGN"},
        )
    _replay_seed_migrations()


#: Data migrations whose reference rows are restored by replaying the
#: migration itself rather than by a copy of its data kept here. Order
#: matters: the authorization grants attach to the roles seeded before them.
#: Not every data migration belongs here - only those inserting reference
#: rows. Backfills that reshape rows the tests create themselves do not.
_SEED_MIGRATIONS = (
    ("api.authorization.migrations.0002_seed_system_roles", "seed"),
    ("api.authorization.migrations.0004_grant_new_admin_feature_permissions", "grant"),
    ("api.operations.migrations.0002_seed_services_and_providers", "seed"),
    ("api.operations.migrations.0003_seed_celery_ai_worker", "seed_worker_service"),
    (
        "api.reviews.migrations.0003_seed_default_quality_check_criteria",
        "seed_default_criteria",
    ),
    (
        "api.reviews.migrations.0005_retire_lesson_quiz_quality_criterion",
        "retire_lesson_quiz_criterion",
    ),
)


def _replay_seed_migrations() -> None:
    """Re-apply seed migrations by calling their own `seed()` functions.

    Copying their rows into this module instead would give the copy room to
    drift from what the migration actually wrote - and for the system roles
    that is the one thing which must not happen, because
    SystemRoleSeedDriftTests detects a registry change with no migration by
    comparing seeded rows against the live registry. A copy here seeded from
    the registry would make that test pass by construction.

    Safe to call repeatedly: every one of these seeds is get_or_create-based,
    and none of them touch the schema_editor argument.
    """

    from importlib import import_module

    from django.apps import apps as live_apps

    for module_path, function_name in _SEED_MIGRATIONS:
        getattr(import_module(module_path), function_name)(live_apps, None)


def transaction_teardown_with_reseed(test_case) -> None:
    """Run TransactionTestCase._fixture_teardown, then restore seed rows.

    Skipped on the mirror databases used by parallel runners - each mirror
    needs its own pass, handled by the runner itself.
    """

    test_case._fixture_teardown_original()
    if connection.alias in test_case.databases:
        reseed_reference_data()

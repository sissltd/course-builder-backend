"""Shared test-suite helpers."""

from django.db import connection


def reseed_reference_data() -> None:
    """Re-apply migration-seeded rows wiped by a TransactionTestCase flush.

    TransactionTestCase tears down by flushing every table - including rows
    inserted by data migrations (quality-check criteria, internal ledger
    accounts, course versions). Any app tested after such a case would find
    its seed data missing. TransactionTestCase subclasses call this at the
    end of _fixture_teardown so the database returns to its post-migration
    state. Keep this in sync with new seed migrations.
    """

    from api.courses.models import CourseVersion
    from api.payments.models.ledgeraccount_models import InternalAccount
    from api.reviews.models import QualityCheckCriterion

    CourseVersion.objects.get_or_create(
        label="1.0", defaults={"is_active": True}
    )
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

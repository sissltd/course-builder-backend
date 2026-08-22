from django.db import transaction
from django.utils import timezone

from api.courses.models import Course
from api.reviews.models import CourseQualityCheck, QualityCheckCriterion
from api.reviews.services.quality_check_service import validate_structural_standards

# Maps each admin-configurable criterion label to the structural failure
# message substring it matches. Criteria without a matcher are manual-only
# (the creator ticks them off by hand); matchers let the automated check
# resolve them from the same failures list the submit-time gate uses.
_AUTOMATED_MATCHERS = {
    "Course title": lambda failures: [],  # title presence is enforced by the model
    "Course description": lambda failures: [
        f for f in failures if "description" in f
    ],
    "Learning objectives": lambda failures: [
        f for f in failures if "learning objectives" in f
    ],
    "Course duration": lambda failures: [
        f for f in failures if "duration" in f
    ],
    "Module count": lambda failures: [
        f for f in failures if "modules" in f and "must have between" in f
    ],
    "Lessons per module": lambda failures: [
        f for f in failures if "lessons" in f and "must have between" in f
    ],
    "Lesson scripts": lambda failures: [
        f for f in failures if "script" in f
    ],
    "Preview video": lambda failures: [
        f for f in failures if "preview video" in f
    ],
    "Terms and conditions": lambda failures: [
        f for f in failures if "Terms" in f
    ],
    "Final assessment": lambda failures: [
        f for f in failures if "final assessment" in f
    ],
}

_DEFAULT_CRITERIA = [
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


def ensure_default_criteria() -> None:
    """Seed the checklist template with the default criteria.

    Idempotent: existing rows (by section+label) are left untouched so any
    admin reordering/retiring survives. Called from the data migration -
    admins can freely add/remove criteria afterwards.
    """

    for section, label in _DEFAULT_CRITERIA:
        QualityCheckCriterion.objects.get_or_create(section=section, label=label)


@transaction.atomic
def refresh_course_quality_checks(*, course: Course) -> list:
    """Recompute a course's results for every active criterion.

    Upserts one CourseQualityCheck row per active criterion: automated
    criteria are resolved from validate_structural_standards' failures
    (a criterion passes when none of its matched failures are present);
    manual criteria stay unchecked until a creator ticks them. Returns the
    refreshed rows.
    """

    failures = validate_structural_standards(course)
    active_criteria = list(
        QualityCheckCriterion.objects.filter(is_active=True)
        .select_related(None)
        .order_by("section", "order_index")
    )

    results = []
    for criterion in active_criteria:
        matched = _AUTOMATED_MATCHERS.get(criterion.label, lambda f: [])(failures)
        is_automated = criterion.label in _AUTOMATED_MATCHERS
        row, _created = CourseQualityCheck.objects.update_or_create(
            course=course,
            criterion=criterion,
            defaults={
                "is_checked": is_automated and not matched,
                "warning_note": matched[0] if matched else "",
                "checked_at": timezone.now() if is_automated else None,
            },
        )
        results.append(row)
    return results


def unresolved_count(*, course: Course) -> int:
    """How many active criteria the course currently fails."""

    return CourseQualityCheck.objects.filter(
        course=course, criterion__is_active=True, is_checked=False
    ).count()

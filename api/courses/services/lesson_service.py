"""Lesson-specific persistence helpers for the course builder."""

from django.db import transaction
from django.utils import timezone

from api.courses.models import LessonRequirement


@transaction.atomic
def replace_requirements(*, lesson, requirements: list[dict], actor) -> list:
    """Replace a lesson's ordered requirement document in one transaction.

    The Figma lesson form saves requirements alongside the lesson. Treating a
    supplied array as the complete current value keeps PATCH retries idempotent;
    omitting the field is handled by the view and leaves existing rows untouched.
    """

    LessonRequirement.objects.filter(lesson=lesson).delete()

    now = timezone.now()
    rows = [
        LessonRequirement(
            lesson=lesson,
            text=requirement["text"],
            order=requirement["order"],
            created_by=actor,
            updated_by=actor,
            created_datetime=now,
            updated_datetime=now,
        )
        for requirement in requirements
    ]
    return LessonRequirement.objects.bulk_create(rows)

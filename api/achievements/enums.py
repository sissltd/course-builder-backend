from django.db import models


class BadgeCriterion(models.TextChoices):
    """What a badge's `required_count` counts, per creator.

    Each value is defined exactly once, as a query, in
    criterion_service.COUNT_EXPRESSIONS - the award engine, the backfill and
    the creator's progress screen all read that one definition.
    """

    COURSES_CREATED = "COURSES_CREATED", "Courses created"
    COURSES_REVIEWED = "COURSES_REVIEWED", "Courses that passed content review"
    COURSES_APPROVED = "COURSES_APPROVED", "Courses approved"
    COURSES_PUBLISHED = "COURSES_PUBLISHED", "Courses published"


#: Sentence fragment per criterion for a badge's requirement line, e.g.
#: "For creators who have created 100 courses". Kept beside the enum so a new
#: criterion can't ship without its wording.
CRITERION_REQUIREMENT_PHRASES = {
    BadgeCriterion.COURSES_CREATED: "created {count} {noun}",
    BadgeCriterion.COURSES_REVIEWED: "had {count} {noun} pass content review",
    BadgeCriterion.COURSES_APPROVED: "had {count} {noun} approved",
    BadgeCriterion.COURSES_PUBLISHED: "published {count} {noun}",
}


class AwardSource(models.TextChoices):
    """How a creator came to hold a badge."""

    AUTOMATIC = "AUTOMATIC", "Automatic"
    MANUAL = "MANUAL", "Manual"
    CARRIED_OVER = "CARRIED_OVER", "Carried over from a deleted badge"

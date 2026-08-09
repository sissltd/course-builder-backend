from django.db import models


class TrackPreference(models.TextChoices):
    """Which production track a category is best suited for.

    AI_PREFERRED is retained for forward compatibility with the SCCS Market
    Intelligence Engine / AI Auto-Production Engine data model, even though
    the AI production track is not implemented yet.
    """

    CREATOR_PREFERRED = "CREATOR_PREFERRED", "Creator Preferred"
    AI_PREFERRED = "AI_PREFERRED", "AI Preferred"
    OPEN = "OPEN", "Open"


class CategoryStatus(models.TextChoices):
    """Whether a category currently accepts new course submissions."""

    ACTIVE = "ACTIVE", "Active"
    INACTIVE = "INACTIVE", "Inactive"


class CategoryDeletionStrategy(models.TextChoices):
    """What to do with a category's courses when the category is deleted.

    Not a model field - this is the decision the admin makes in the delete
    confirmation dialog once they have been warned how many courses are
    affected. Deleting a category with no courses needs no strategy at all.
    """

    REASSIGN = "REASSIGN", "Move courses to another category"
    DELETE_COURSES = "DELETE_COURSES", "Delete the courses too"

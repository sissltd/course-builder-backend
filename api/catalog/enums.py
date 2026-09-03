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
    """Whether a category currently accepts new course submissions.

    ARCHIVED is distinct from INACTIVE: inactive is a temporary pause an
    admin can flip back at will, archived is a retirement that hides the
    category from creators entirely while keeping every historical course
    and its payout intact.
    """

    ACTIVE = "ACTIVE", "Active"
    INACTIVE = "INACTIVE", "Inactive"
    ARCHIVED = "ARCHIVED", "Archived"


class CategoryDeletionStrategy(models.TextChoices):
    """What to do with a category's courses when the category is deleted.

    Not a model field - this is the decision the admin makes in the delete
    confirmation dialog once they have been warned how many courses are
    affected. Deleting a category with no courses needs no strategy at all.
    """

    REASSIGN = "REASSIGN", "Move courses to another category"
    DELETE_COURSES = "DELETE_COURSES", "Delete the courses too"


class CategoryRequestStatus(models.TextChoices):
    """Lifecycle status of a creator's request for a new Category."""

    PENDING = "PENDING", "Pending"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"


class ReservationStatus(models.TextChoices):
    """Lifecycle status of a creator's request to reserve a Topic (PRD BR-007)."""

    PENDING = "PENDING", "Pending"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"

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


class CourseStatus(models.TextChoices):
    """Course lifecycle status (SCCS PRD v3.5 Section 8, Flow A).

    REJECTED is kept for schema/display completeness (e.g. reporting on
    ReviewAction history) but is never persisted directly on Course.status:
    a rejection immediately reverts the course to DRAFT so the creator can
    revise and resubmit, per PRD wording "Returns to Draft. Creator revises."
    """

    DRAFT = "DRAFT", "Draft"
    SUBMITTED = "SUBMITTED", "Submitted"
    IN_REVIEW = "IN_REVIEW", "In Review"
    APPROVED = "APPROVED", "Approved"
    PUBLISHED = "PUBLISHED", "Published"
    REJECTED = "REJECTED", "Rejected"


class AssessmentLevel(models.TextChoices):
    """Which course entity an Assessment (quiz) belongs to."""

    LESSON = "LESSON", "Lesson"
    MODULE = "MODULE", "Module"
    COURSE = "COURSE", "Course"


class ReviewActionType(models.TextChoices):
    """The decision recorded by a reviewer on a ReviewAction."""

    APPROVE = "APPROVE", "Approve"
    REJECT = "REJECT", "Reject"

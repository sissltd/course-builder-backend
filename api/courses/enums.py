from django.db import models


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


class QuestionType(models.TextChoices):
    """A quiz question's answer format."""

    MULTIPLE_CHOICE = "MULTIPLE_CHOICE", "Question Choice"
    ESSAY = "ESSAY", "Essay Question"


class ReviewActionType(models.TextChoices):
    """The decision recorded by a reviewer on a ReviewAction."""

    APPROVE = "APPROVE", "Approve"
    REJECT = "REJECT", "Reject"


class CategoryRequestStatus(models.TextChoices):
    """Lifecycle status of a creator's request for a new Category."""

    PENDING = "PENDING", "Pending"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"


class DifficultyLevel(models.TextChoices):
    """Course difficulty level, self-reported by the creator."""

    BEGINNER = "BEGINNER", "Beginner"
    INTERMEDIATE = "INTERMEDIATE", "Intermediate"
    ADVANCED = "ADVANCED", "Advanced"


class ReservationStatus(models.TextChoices):
    """Lifecycle status of a creator's request to reserve a Topic (PRD BR-007)."""

    PENDING = "PENDING", "Pending"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"

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
    QA_VERIFICATION = "QA_VERIFICATION", "QA Verification"
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


class CourseSource(models.TextChoices):
    """Where a course originated.  AI is reserved for the MIE/APE import."""

    CREATOR = "CREATOR", "Creator"
    AI = "AI", "AI"


class ReviewStage(models.TextChoices):
    """The two mandatory quality gates for new course submissions."""

    CONTENT = "CONTENT", "Content Review"
    QA = "QA", "QA Verification"


class QualityCheckStatus(models.TextChoices):
    NOT_RUN = "NOT_RUN", "Not Run"
    PASS = "PASS", "Pass"
    WARNING = "WARNING", "Warning"
    FAIL = "FAIL", "Fail"


class QualityRiskLevel(models.TextChoices):
    LOW = "LOW", "Low"
    MEDIUM = "MEDIUM", "Medium"
    HIGH = "HIGH", "High"
    CRITICAL = "CRITICAL", "Critical"


class FindingSeverity(models.TextChoices):
    INFO = "INFO", "Info"
    WARNING = "WARNING", "Warning"
    ERROR = "ERROR", "Error"


class MediaAssetKind(models.TextChoices):
    VIDEO = "VIDEO", "Video"
    AUDIO = "AUDIO", "Audio"
    SUBTITLE = "SUBTITLE", "Subtitle"
    THUMBNAIL = "THUMBNAIL", "Thumbnail"
    PREVIEW_VIDEO = "PREVIEW_VIDEO", "Preview Video"


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


class AppealStatus(models.TextChoices):
    """Lifecycle status of a creator's appeal against a course rejection
    (PRD Section 12: "Creator disputes rejection... Escalated to Senior
    Reviewer. Decision is final and logged.")."""

    PENDING = "PENDING", "Pending"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"

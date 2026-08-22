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
    NEEDS_REVISION = "NEEDS_REVISION", "Needs Revision"
    APPROVED = "APPROVED", "Approved"
    PUBLISHED = "PUBLISHED", "Published"
    ARCHIVED = "ARCHIVED", "Archived"
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


class DifficultyLevel(models.TextChoices):
    """Course difficulty level, self-reported by the creator."""

    BEGINNER = "BEGINNER", "Beginner"
    INTERMEDIATE = "INTERMEDIATE", "Intermediate"
    ADVANCED = "ADVANCED", "Advanced"


class CourseSourceType(models.TextChoices):
    """Where a course's content originated - the "Type" badge shown on
    course details. DEVELOPER_API is the future MIE ingestion path
    (external developers submitting courses via the Studio API); it's
    reserved now so later MIE rows can FK onto existing courses."""

    CREATOR_UPLOADED = "CREATOR_UPLOADED", "Creator Uploaded"
    AI_GENERATED = "AI_GENERATED", "AI Generated"
    DEVELOPER_API = "DEVELOPER_API", "Developer API"


class LessonContentType(models.TextChoices):
    """A lesson's primary media format - matches the three "Add lesson"
    buttons (Video, Quiz, Text) in the builder."""

    VIDEO = "VIDEO", "Video"
    QUIZ = "QUIZ", "Quiz"
    TEXT = "TEXT", "Text"


class MediaSource(models.TextChoices):
    """Where a piece of media (thumbnail, lesson image) came from - the
    options offered by the "Add Media" modal."""

    UPLOAD = "UPLOAD", "Upload"
    GOOGLE_DRIVE = "GOOGLE_DRIVE", "Google Drive"
    YOUTUBE = "YOUTUBE", "YouTube"
    DROPBOX = "DROPBOX", "Dropbox"
    LINK = "LINK", "Link"


class AppealStatus(models.TextChoices):
    """Lifecycle status of a creator's appeal against a course rejection
    (PRD Section 12: "Creator disputes rejection... Escalated to Senior
    Reviewer. Decision is final and logged.")."""

    PENDING = "PENDING", "Pending"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"

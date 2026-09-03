from django.db import models


class CostCategory(models.TextChoices):
    """What a production cost was spent on.

    Keeps the admin cost charts breakable by driver rather than showing a
    single opaque total - the expensive line items in AI production are
    voice and video generation, so they get their own categories.
    """

    VOICE = "VOICE", "Voice Generation"
    VIDEO = "VIDEO", "Video Generation"
    TEXT = "TEXT", "Text Generation"
    STORAGE = "STORAGE", "Storage"
    REVIEW = "REVIEW", "Human Review"
    OTHER = "OTHER", "Other"


class ServiceStatus(models.TextChoices):
    """Operational state of a monitored dependency."""

    OPERATIONAL = "OPERATIONAL", "Operational"
    DEGRADED = "DEGRADED", "Degraded"
    DOWN = "DOWN", "Down"


class ServicePriority(models.TextChoices):
    """How urgently a degraded service needs attention.

    Drives the Priority column on the System Health table; set per service
    rather than derived, because criticality is a judgment about the
    business, not something latency can tell you.
    """

    NORMAL = "NORMAL", "Normal"
    MEDIUM = "MEDIUM", "Medium"
    HIGH = "HIGH", "High"


class PipelineStage(models.TextChoices):
    """The ordered stages an AI-produced course passes through.

    Order matters: the admin pipeline view renders these as a funnel, and
    `STAGE_ORDER` below is the single place that sequence is defined.
    """

    TOPIC_INTAKE = "TOPIC_INTAKE", "Topic Intake"
    CURRICULUM = "CURRICULUM", "Curriculum"
    CONTENT_GENERATION = "CONTENT_GENERATION", "Content Generation"
    ASSESSMENT_BUILDER = "ASSESSMENT_BUILDER", "Assessment Builder"
    MEDIA_PRODUCTION = "MEDIA_PRODUCTION", "Media Production"
    PREVIEW_VIDEO = "PREVIEW_VIDEO", "Preview Video"
    ASSEMBLY_PACKAGING = "ASSEMBLY_PACKAGING", "Assembly and Packaging"
    AUTO_QA = "AUTO_QA", "Auto-QA"


STAGE_ORDER = [choice.value for choice in PipelineStage]
"""Funnel order for the pipeline view. Derived from the enum declaration
order so the two can never disagree."""


class PipelineJobStatus(models.TextChoices):
    """Where one pipeline job currently sits."""

    QUEUED = "QUEUED", "Queued"
    RUNNING = "RUNNING", "Running"
    COMPLETED = "COMPLETED", "Completed"
    FAILED = "FAILED", "Failed"
    RETRYING = "RETRYING", "Retrying"


class ProviderKind(models.TextChoices):
    """What an external production provider supplies."""

    VOICE = "VOICE", "Voice"
    VIDEO = "VIDEO", "Video"
    TEXT = "TEXT", "Text"
    FALLBACK = "FALLBACK", "Fallback"


class EnrollmentStatus(models.TextChoices):
    """A learner's relationship with a course they enrolled in."""

    ACTIVE = "ACTIVE", "Active"
    COMPLETED = "COMPLETED", "Completed"
    DROPPED = "DROPPED", "Dropped"

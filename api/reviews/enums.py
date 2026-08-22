from django.db import models


class ReviewActionType(models.TextChoices):
    """The decision recorded by a reviewer on a ReviewAction."""

    APPROVE = "APPROVE", "Approve"
    REJECT = "REJECT", "Reject"


class ReviewStage(models.TextChoices):
    """The two mandatory quality gates for new course submissions."""

    CONTENT = "CONTENT", "Content Review"
    QA = "QA", "QA Verification"


class QualityCheckStatus(models.TextChoices):
    """Result state of an automated or provider-supplied quality check."""

    NOT_RUN = "NOT_RUN", "Not Run"
    PASS = "PASS", "Pass"
    WARNING = "WARNING", "Warning"
    FAIL = "FAIL", "Fail"


class QualityRiskLevel(models.TextChoices):
    """Overall risk classification of a course from its quality evidence."""

    LOW = "LOW", "Low"
    MEDIUM = "MEDIUM", "Medium"
    HIGH = "HIGH", "High"
    CRITICAL = "CRITICAL", "Critical"


class FindingSeverity(models.TextChoices):
    """Severity of a single quality finding or review comment."""

    INFO = "INFO", "Info"
    WARNING = "WARNING", "Warning"
    ERROR = "ERROR", "Error"


class MediaAssetKind(models.TextChoices):
    """The kind of media asset tracked for QA verification."""

    VIDEO = "VIDEO", "Video"
    AUDIO = "AUDIO", "Audio"
    SUBTITLE = "SUBTITLE", "Subtitle"
    THUMBNAIL = "THUMBNAIL", "Thumbnail"
    PREVIEW_VIDEO = "PREVIEW_VIDEO", "Preview Video"

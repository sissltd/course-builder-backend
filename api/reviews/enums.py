from django.db import models


class ReviewActionType(models.TextChoices):
    """The decision recorded by a reviewer on a ReviewAction."""

    APPROVE = "APPROVE", "Approve"
    REJECT = "REJECT", "Reject"


class ReviewStage(models.TextChoices):
    """The review seats a new course submission passes through, in order.

    The first three are content seats, each held by a different person;
    QA is the media gate that follows them. CONTENT keeps the value it had
    when content review was a single seat, so existing rows need no
    rename - only its label moved to "First Review".
    """

    CONTENT = "CONTENT", "First Review"
    SECOND_REVIEW = "SECOND_REVIEW", "Second Review"
    VERIFICATION = "VERIFICATION", "Verification"
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

from django.db import models


class ProductionRunStatus(models.TextChoices):
    """Where one Production Engine run sits.

    QUEUED runs wait for the engine (and for `production_enabled`). BLOCKED
    runs were stopped by the budget and need an admin to retry them once the
    budget allows. COMPLETED means every step this engine version has has
    run; later versions add steps, and the course moves on only once the
    video is delivered.
    """

    QUEUED = "QUEUED", "Queued"
    RUNNING = "RUNNING", "Running"
    BLOCKED = "BLOCKED", "Blocked by budget"
    COMPLETED = "COMPLETED", "Completed"
    FAILED = "FAILED", "Failed"
    CANCELLED = "CANCELLED", "Cancelled"


#: Runs that still hold a course. At most one per course (a DB constraint).
ACTIVE_RUN_STATUSES = (
    ProductionRunStatus.QUEUED,
    ProductionRunStatus.RUNNING,
    ProductionRunStatus.BLOCKED,
)

#: Runs an admin may send back to the queue.
RETRYABLE_RUN_STATUSES = (ProductionRunStatus.FAILED, ProductionRunStatus.BLOCKED)


class SceneType(models.TextChoices):
    """The template a storyboard scene is rendered with. Faceless by design:
    no type puts a presenter on screen."""

    TITLE = "TITLE", "Title card"
    BULLETS = "BULLETS", "Key points"
    DIAGRAM = "DIAGRAM", "Diagram"
    CODE = "CODE", "Code"
    IMAGE = "IMAGE", "Illustration"
    QUOTE = "QUOTE", "Quote or definition"
    COMPARISON = "COMPARISON", "Comparison"
    RECAP = "RECAP", "Recap"
    BROLL = "BROLL", "Motion footage"


class RunKind(models.TextChoices):
    """What a run produces. VIDEO runs make (or rework) a course's lesson
    videos, trailer and thumbnail and hand them to review; PACKAGE runs make
    the final package of a published course and deliver it to its channels."""

    VIDEO = "VIDEO", "Video"
    PACKAGE = "PACKAGE", "Final package"


class AssetKind(models.TextChoices):
    """A file the engine made. Each is stored once under the hash of what
    went into it, so unchanged work is reused rather than paid for again."""

    NARRATION = "NARRATION", "Scene narration"
    SLIDE = "SLIDE", "Scene visual"
    BROLL = "BROLL", "B-roll clip"
    LESSON_VIDEO = "LESSON_VIDEO", "Lesson video"
    TRAILER = "TRAILER", "Course trailer"
    THUMBNAIL = "THUMBNAIL", "Course thumbnail"
    PACKAGE = "PACKAGE", "Canonical course package"
    SCORM_12 = "SCORM_12", "SCORM 1.2 export"
    SCORM_2004 = "SCORM_2004", "SCORM 2004 export"
    UPLOAD_KIT = "UPLOAD_KIT", "Channel upload kit"


#: Asset kinds offered as downloads once a course is packaged.
PACKAGE_ASSET_KINDS = (
    AssetKind.PACKAGE,
    AssetKind.SCORM_12,
    AssetKind.SCORM_2004,
    AssetKind.UPLOAD_KIT,
)


class ReworkAction(models.TextChoices):
    """What the engine redoes for a lesson a reviewer flagged."""

    REVOICE = "REVOICE", "Record the narration again"
    RESTORYBOARD = "RESTORYBOARD", "Plan and draw the scenes again"
    RERENDER = "RERENDER", "Mix and render the video again"


class DeliveryMethod(models.TextChoices):
    """How a channel receives a course.

    API_PUSH sends the mapped payload to the channel's API (the URL and key
    come from the server's environment, never from the mapping). UPLOAD_KIT
    builds a downloadable kit that a person uploads; the admin then records
    the channel's course id.
    """

    API_PUSH = "API_PUSH", "API push"
    UPLOAD_KIT = "UPLOAD_KIT", "Upload kit"

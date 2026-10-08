from rest_framework import exceptions, status


class ModuleLocked(exceptions.APIException):
    """Raised when a module edit is attempted while another user holds an
    active edit lock on it (SCCS PRD Section 14). Flows through the
    existing drf_standardized_errors pipeline unchanged - only the
    status_code differs from ValidationError/PermissionDenied."""

    status_code = status.HTTP_423_LOCKED
    default_detail = (
        "This module is locked for editing by another user or the course creator."
    )
    default_code = "locked"


class AIDispatchUnavailable(exceptions.APIException):
    """Raised when a generation job cannot be handed to Celery."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "AI generation could not be queued. Please try again."
    default_code = "ai_dispatch_unavailable"


class CourseNotAwaitingVideo(exceptions.APIException):
    """Raised when a video step is attempted on a course that is not waiting
    for its video. 409 because the request is well-formed and conflicts with
    where the course is in the staged review flow."""

    status_code = status.HTTP_409_CONFLICT
    default_detail = "This course is not waiting for its video."
    default_code = "course_not_awaiting_video"


class VideoProviderConflict(exceptions.APIException):
    """Raised when the caller is not the party that supplies this course's
    video (the creator, the developer, or the production engine)."""

    status_code = status.HTTP_409_CONFLICT
    default_detail = "The video for this course is supplied by someone else."
    default_code = "video_provider_conflict"

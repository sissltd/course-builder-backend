"""Admin request and response shapes for the course a developer pushed."""

from rest_framework import serializers

from api.courses.enums import CourseStatus
from api.mie.enums import AssignableVideoProducer

ARCHIVE_REASON_MAX_LENGTH = 1000
"""Long enough for a paragraph to the developer; it travels in the webhook."""


class VideoAssignmentSerializer(serializers.Serializer):
    """Who makes the video of a developer's course whose text has passed."""

    video_producer = serializers.ChoiceField(
        choices=AssignableVideoProducer.choices,
        help_text=(
            "PRODUCTION_ENGINE to have the platform's engine make the video "
            "(a production run is queued at once), or DEVELOPER to have the "
            "developer send it (any run in progress is cancelled)."
        ),
    )


class CourseArchiveSerializer(serializers.Serializer):
    """Why a developer's course is being archived."""

    reason = serializers.CharField(
        max_length=ARCHIVE_REASON_MAX_LENGTH,
        help_text=(
            "Why the course will not go further, in words for the developer. "
            "Sent to them as course.archive_reason on COURSE_ARCHIVED."
        ),
    )


class AdminDeveloperCourseSerializer(serializers.Serializer):
    """The course a developer pushed for one idea, after an admin move."""

    submission_id = serializers.UUIDField(help_text="The idea the course belongs to.")
    submission_reference = serializers.CharField(
        help_text="The idea's public reference (suffix tracks the idea's status)."
    )
    course_id = serializers.UUIDField(help_text="The course's id.")
    title = serializers.CharField(help_text="Course title.")
    status = serializers.ChoiceField(
        choices=CourseStatus.choices, help_text="Where the course is now."
    )
    video_producer = serializers.ChoiceField(
        choices=AssignableVideoProducer.choices,
        allow_null=True,
        help_text=(
            "Who makes the video: PRODUCTION_ENGINE or DEVELOPER, or null "
            "while it is still to be assigned."
        ),
    )

"""Admin moves on the course a developer pushed for an approved idea.

Two moves the review flow does not make on its own:

* Assigning the video. Once a course's text passes the first seat it waits
  for its video (AWAITING_VIDEO). An admin hands that video to the developer
  or to the production engine - required for an account with
  `requires_video_assignment` on, whose course waits with no producer, and
  allowed for any developer course until its video is submitted.
* Archiving. A course that will not be taken any further is archived: it
  leaves every review queue, any engine run on it is cancelled, and the
  developer can no longer push it.

Both are addressed by the idea (submission) id, the id the developer holds,
and both tell the developer with a COURSE_* webhook.
"""

from django.db import transaction
from rest_framework import exceptions

from api.authentication.services.activity_service import log_activity
from api.authorization import codenames
from api.authorization.services import permission_service
from api.courses.enums import CourseStatus, VideoProvider
from api.courses.exceptions import CourseNotAwaitingVideo, VideoProviderConflict
from api.courses.models import Course
from api.mie.enums import MieSourceType, WebhookEventType
from api.mie.exceptions import CourseNotArchivable
from api.mie.models import CourseSubmission
from api.mie.services import course_push_service
from api.payments.services import course_payout_service
from api.production.services import production_service
from api.users.enums import UserActivityActionEnums, UserActivityCategoryEnums

ARCHIVABLE_STATUSES = frozenset(
    {
        CourseStatus.DRAFT,
        CourseStatus.SUBMITTED,
        CourseStatus.IN_REVIEW,
        CourseStatus.NEEDS_REVISION,
        CourseStatus.AWAITING_VIDEO,
        CourseStatus.QA_VERIFICATION,
    }
)
"""Statuses a developer course can be archived from. APPROVED (passed QA,
and paid) and PUBLISHED are past what archiving can undo."""


def assign_video(*, actor, submission_id, producer: str) -> Course:
    """Hand the video of the idea's course to `producer` (an
    AssignableVideoProducer value) and tell the developer.

    The course must be AWAITING_VIDEO. Choosing the production engine queues
    a run; choosing the developer cancels any run in progress. The producer
    also sets the price the course is paid at (with or without video).
    Changeable until the video is submitted.

    Raises NotFound for an unknown idea or one with no course,
    CourseNotAwaitingVideo (409) when the course is not waiting for its
    video, VideoProviderConflict (409) when handing the crawler's course to
    its developer, and ValidationError when the video is already assigned to
    `producer` - nothing changes and no webhook fires.
    """

    permission_service.require_permission(actor, codenames.MIE_MANAGE_CONSOLE)
    with transaction.atomic():
        submission, course = _lock_course(submission_id=submission_id)
        if course.status != CourseStatus.AWAITING_VIDEO:
            raise CourseNotAwaitingVideo()
        if (
            producer == VideoProvider.DEVELOPER
            and submission.developer.source_type == MieSourceType.SYSTEM
        ):
            raise VideoProviderConflict(
                "The platform's crawler has no video to send; the production "
                "engine makes the video for its courses."
            )
        if course.video_provider == producer:
            raise exceptions.ValidationError(
                {"video_producer": [f"The video is already assigned to {producer}."]}
            )

        course.video_provider = producer
        # Who makes the video decides the price: with or without video.
        course_payout_service.refresh_expected_payout(course)
        course.updated_by = actor
        course.save(
            update_fields=[
                "video_provider",
                "payout_type",
                "creator_price_snapshot",
                "updated_by",
                "updated_datetime",
            ]
        )
        if producer == VideoProvider.PRODUCTION_ENGINE:
            production_service.request_production(course=course, actor=actor)
        else:
            production_service.cancel_active_run(
                course=course,
                actor=actor,
                reason="An admin assigned the video to the developer.",
            )
        log_activity(
            user=actor,
            category=UserActivityCategoryEnums.PRODUCTION,
            action=UserActivityActionEnums.COURSE_VIDEO_ASSIGNED,
            summary=(
                f"You assigned the video for '{course.title}' to "
                f"{VideoProvider(producer).label}."
            ),
            details={"submission_id": str(submission.id), "video_producer": producer},
            target=course,
        )
        course_push_service.record_course_event(
            course=course,
            event_type=WebhookEventType.COURSE_VIDEO_ASSIGNED,
            details={"video_producer": producer},
        )
    return course


def archive_course(*, actor, submission_id, reason: str) -> Course:
    """Archive the idea's course for good and tell the developer why.

    The course leaves every review queue and any engine run on it is
    cancelled; the developer can no longer push it. A payout already made
    (engine-made video is paid at Second Review) is not reversed. The idea
    itself keeps its status.

    Raises NotFound for an unknown idea or one with no course, and
    CourseNotArchivable (409) for a course that is already archived, or
    APPROVED or PUBLISHED.
    """

    permission_service.require_permission(actor, codenames.MIE_MANAGE_CONSOLE)
    with transaction.atomic():
        submission, course = _lock_course(submission_id=submission_id)
        if course.status not in ARCHIVABLE_STATUSES:
            raise CourseNotArchivable(
                f"A {course.get_status_display()} course cannot be archived."
            )

        course.status = CourseStatus.ARCHIVED
        course.review_stage = ""
        course.updated_by = actor
        course.save(
            update_fields=["status", "review_stage", "updated_by", "updated_datetime"]
        )
        production_service.cancel_active_run(
            course=course, actor=actor, reason="The course was archived."
        )
        log_activity(
            user=actor,
            category=UserActivityCategoryEnums.COURSE,
            action=UserActivityActionEnums.COURSE_ARCHIVED,
            summary=f"You archived '{course.title}'.",
            details={"submission_id": str(submission.id), "reason": reason},
            target=course,
        )
        course_push_service.record_course_event(
            course=course,
            event_type=WebhookEventType.COURSE_ARCHIVED,
            details={"archive_reason": reason},
        )
    return course


def course_payload(*, course: Course, submission: CourseSubmission) -> dict:
    """The AdminDeveloperCourseSerializer shape for one course."""

    return {
        "submission_id": submission.id,
        "submission_reference": submission.public_reference,
        "course_id": course.id,
        "title": course.title,
        "status": course.status,
        "video_producer": course.video_provider or None,
    }


def _lock_course(*, submission_id) -> tuple[CourseSubmission, Course]:
    """The idea and its course, both row-locked so a concurrent push, review
    or second admin move waits for this one."""

    submission = (
        CourseSubmission.objects.select_for_update()
        .select_related("developer")
        .filter(id=submission_id)
        .first()
    )
    if submission is None:
        raise exceptions.NotFound("Submission not found.")
    if submission.resulting_course_id is None:
        raise exceptions.NotFound("No course has been pushed for this idea yet.")
    course = Course.objects.select_for_update().get(pk=submission.resulting_course_id)
    return submission, course

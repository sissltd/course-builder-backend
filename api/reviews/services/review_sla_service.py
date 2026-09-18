"""Watching courses that are waiting on a review decision.

Two admin-facing rules share one pass over the queue, because both ask the
same question - how long has this course been waiting since `submitted_at`:

- past `sla_red_threshold_hours`, alert whoever can approve courses;
- past `auto_flag_after_hours`, flag the course for attention.

Both are advisory. Nothing here changes a course's status, releases a seat
or blocks a transition; the pipeline runs exactly as it would have.
"""

import logging
from datetime import timedelta

from django.utils import timezone

from api.authorization import codenames
from api.authorization.services import permission_service
from api.courses.enums import CourseStatus
from api.courses.models import Course
from api.notification.models import Notification
from api.platform.services import platform_settings_service

logger = logging.getLogger(__name__)

#: Statuses where a course is waiting on someone's decision. A course that
#: has left these is settled, so it is neither alerted on nor flagged.
AWAITING_DECISION = (
    CourseStatus.SUBMITTED,
    CourseStatus.IN_REVIEW,
    CourseStatus.QA_VERIFICATION,
)

FLAG_REASON = "No review decision within the configured window."


def run_sla_sweep(*, now=None) -> dict:
    """Alert on breached courses, flag stalled ones, unflag settled ones.

    Returns a count per action. Safe to run on any cadence and safe to
    overlap with itself: each course carries the marker that excludes it
    from the next pass, so the work is claimed, not repeated.
    """

    now = now or timezone.now()
    settings_row = platform_settings_service.get_settings()

    return {
        "alerted": _alert_breached(now=now, hours=settings_row.sla_red_threshold_hours),
        "flagged": _flag_stalled(now=now, hours=settings_row.auto_flag_after_hours),
        "unflagged": _unflag_settled(),
    }


def _alert_breached(*, now, hours: int) -> int:
    if not hours:
        return 0

    breached = list(
        Course.objects.filter(
            status__in=AWAITING_DECISION,
            submitted_at__isnull=False,
            submitted_at__lte=now - timedelta(hours=hours),
            sla_red_alerted_at__isnull=True,
        ).select_related("creator")
    )
    if not breached:
        return 0

    # Resolved once, not per course: this is the expensive query and the
    # recipients are the same for every breach in the pass.
    #
    # exclude() rather than filter(sla_red_critical_alert=True) because a
    # NotificationPreference row is created lazily - an admin who has never
    # touched their preferences must still be told.
    admins = list(
        permission_service.users_with_permission(codenames.COURSES_APPROVE).exclude(
            notification_preference__sla_red_critical_alert=False
        )
    )
    if admins:
        for course in breached:
            waited = int((now - course.submitted_at).total_seconds() // 3600)
            Notification.emit_in_app_notification(
                receivers=admins,
                title="Course overdue for review",
                content=(
                    f"'{course.title}' has been waiting {waited} hours for a "
                    f"review decision."
                ),
                metadata={"course_id": course.id, "hours_waiting": waited},
            )

    for course in breached:
        course.sla_red_alerted_at = now
    Course.objects.bulk_update(breached, ["sla_red_alerted_at"])
    return len(breached)


def _flag_stalled(*, now, hours: int) -> int:
    if not hours:
        return 0

    stalled = list(
        Course.objects.filter(
            status__in=AWAITING_DECISION,
            submitted_at__isnull=False,
            submitted_at__lte=now - timedelta(hours=hours),
            flagged_at__isnull=True,
        )
    )
    for course in stalled:
        course.flagged_at = now
        course.flag_reason = FLAG_REASON
    if stalled:
        Course.objects.bulk_update(stalled, ["flagged_at", "flag_reason"])
    return len(stalled)


def _unflag_settled() -> int:
    """Clear flags on courses that have since left the queue.

    start_review_cycle clears the flag when a course goes round again, but a
    course that was approved, published or archived never passes through it.
    Doing it here keeps that knowledge in one place instead of in every
    decision path in review_service.
    """

    return (
        Course.objects.filter(flagged_at__isnull=False)
        .exclude(status__in=AWAITING_DECISION)
        .update(flagged_at=None, flag_reason="")
    )

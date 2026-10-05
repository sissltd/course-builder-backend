"""Telling admins when a course changes status.

Backs the admin "Course Update" toggle (NotificationPreference.course_update).
The statuses that count are the ones a course reaches as an outcome:
submitted, approved, rejected and published. Moving between review seats
is not an outcome, so it is not announced here.
"""

from api.authorization import codenames
from api.authorization.services import permission_service
from api.courses.models import Course
from api.notification.models import Notification
from api.users.models import User


def notify_course_status_change(
    *, course: Course, actor: User | None, title: str, content: str
) -> None:
    """In-app notice to the admin tier, everyone but `actor`.

    The admin tier is whoever holds `courses.assign` (Admin, Approver and
    Super Admin by default) - the same line review_service.is_admin_tier
    draws. `courses.approve` would be wrong here: every reviewer role holds
    it, and the toggle is an admin one.

    Admins who switched `course_update` off are skipped. exclude() rather
    than filter(course_update=True), because the preference row is created
    lazily and an admin without one must still be told. Costs one query for
    the recipients, whatever their number, plus the bulk emit.
    """

    recipients = permission_service.users_with_permission(
        codenames.COURSES_ASSIGN
    ).exclude(notification_preference__course_update=False)
    if actor is not None:
        recipients = recipients.exclude(pk=actor.pk)
    recipients = list(recipients)
    if not recipients:
        return

    Notification.emit_in_app_notification(
        receivers=recipients,
        title=title,
        content=content,
        metadata={"course_id": course.id, "status": course.status},
    )

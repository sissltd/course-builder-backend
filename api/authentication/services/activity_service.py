from django.utils import timezone

from api.users.enums import UserActivityCategoryEnums
from api.users.models import UserActivityLog


def log_activity(
    *,
    user,
    category: str,
    action: str,
    summary: str,
    actor_user=None,
    request=None,
    details: dict | None = None,
    target=None,
):
    """Create a UserActivityLog row for any activity domain (auth, course
    lifecycle, configuration changes, ...).

    `user` is whose activity log this appears on (e.g. the reviewer who
    claimed a course); `actor_user` defaults to `user` since most activity is
    self-performed. Pulls IP/user-agent/method/path from `request` (a DRF/
    Django HttpRequest) when given - callers without a request in scope (e.g.
    internal service-to-service calls) can omit it. `target` is an optional
    model instance (e.g. a Course) stored via the generic FK.
    """

    ip_address = None
    user_agent = ""
    request_method = ""
    request_path = ""

    if request is not None:
        ip_address = request.META.get("REMOTE_ADDR")
        user_agent = request.META.get("HTTP_USER_AGENT", "")
        request_method = request.method
        request_path = request.path

    return UserActivityLog.objects.create(
        user=user,
        actor_user=actor_user or user,
        category=category,
        action=action,
        summary=summary,
        details=details or {},
        activity_datetime=timezone.now(),
        target=target,
        ip_address=ip_address,
        user_agent=user_agent,
        request_method=request_method,
        request_path=request_path,
    )


def log_auth_activity(
    *, user, action: str, summary: str, request=None, details: dict | None = None
):
    """Create an AUTH-category UserActivityLog row. Thin wrapper over
    log_activity, kept for the many existing call sites."""

    return log_activity(
        user=user,
        category=UserActivityCategoryEnums.AUTH,
        action=action,
        summary=summary,
        request=request,
        details=details,
    )

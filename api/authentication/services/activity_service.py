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
    user_agent: str | None = "",
    ip_address: str | None = "",
):
    """Create a UserActivityLog row for any activity domain (auth, course
    lifecycle, configuration changes, ...).

    `user` is whose activity log this appears on (e.g. the reviewer who
    claimed a course); `actor_user` defaults to `user` since most activity is
    self-performed. Pulls IP/user-agent/method/path from `request` (a DRF/
    Django HttpRequest) when given - callers without a request in scope (e.g.
    internal service-to-service calls) can omit it. `target` is an optional
    model instance (e.g. a Course) stored via the generic FK.

    This function carries optional `user_agent` and `ip_address` parameters,
    allowing for explicit logging of these values when a request object is not
    available (such as the service layer). If provided, these values will be
    used in the log entry; otherwise, they will be extracted from the request
    if it is present.
    """

    request_method = ""
    request_path = ""

    if request is not None:
        ip_address = ip_address or request.META.get("REMOTE_ADDR")
        user_agent = user_agent or request.META.get("HTTP_USER_AGENT", "")
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


def bulk_log_activity(*, entries: list[dict]) -> list:
    """Write many UserActivityLog rows in one INSERT.

    For service code that changes state for many users at once (e.g. a badge
    backfill), where calling log_activity per user would issue one query per
    row. Each entry takes log_activity's keyword names - `user`, `category`,
    `action`, `summary`, and optionally `actor_user`, `details`, `target`.
    There is no request in scope for bulk work, so request metadata is left
    blank.
    """

    from django.contrib.contenttypes.models import ContentType

    now = timezone.now()
    rows = []
    for entry in entries:
        target = entry.get("target")
        rows.append(
            UserActivityLog(
                user=entry["user"],
                actor_user=entry.get("actor_user") or entry["user"],
                category=entry["category"],
                action=entry["action"],
                summary=entry["summary"],
                details=entry.get("details") or {},
                activity_datetime=now,
                # get_for_model is served from ContentType's in-process cache
                # after its first lookup, so this is not a query per row.
                content_type=(
                    ContentType.objects.get_for_model(target) if target else None
                ),
                object_id=str(target.pk) if target else None,
            )
        )
    return UserActivityLog.objects.bulk_create(rows)

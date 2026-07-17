from django.utils import timezone

from api.users.enums import UserActivityCategoryEnums
from api.users.models import UserActivityLog


def log_auth_activity(
    *, user, action: str, summary: str, request=None, details: dict | None = None
):
    """Create a UserActivityLog row for an authentication event.

    category is always AUTH. Pulls IP/user-agent/method/path from `request`
    (a DRF/Django HttpRequest) when given - callers without a request in scope
    (e.g. internal service-to-service calls) can omit it.
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
        actor_user=user,
        category=UserActivityCategoryEnums.AUTH,
        action=action,
        summary=summary,
        details=details or {},
        activity_datetime=timezone.now(),
        ip_address=ip_address,
        user_agent=user_agent,
        request_method=request_method,
        request_path=request_path,
    )

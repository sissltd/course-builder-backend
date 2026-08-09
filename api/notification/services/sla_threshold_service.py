from api.notification.services.notification_preference_service import (
    get_or_create_preference,
)
from api.platform.services import platform_settings_service
from api.users.models import User


def get_effective_thresholds(*, user: User) -> tuple[int, int]:
    """Return (amber_hours, red_hours) for `user`: their own override if set,
    else the platform-wide default. Single source of truth for anything that
    needs to reason about SLA urgency (e.g. the review queue's SLA_URGENCY
    sort), independent of whether the SLA-breach alerting engine exists."""

    preference = get_or_create_preference(user=user)
    settings_row = platform_settings_service.get_settings()

    amber_hours = (
        preference.sla_amber_threshold_hours_override
        if preference.sla_amber_threshold_hours_override is not None
        else settings_row.sla_amber_threshold_hours
    )
    red_hours = (
        preference.sla_red_threshold_hours_override
        if preference.sla_red_threshold_hours_override is not None
        else settings_row.sla_red_threshold_hours
    )
    return amber_hours, red_hours

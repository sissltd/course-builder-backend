from api.notification.models import NotificationPreference
from api.users.models import User

UPDATABLE_FIELDS = {
    "new_course_assigned",
    "escalation_assigned",
    "creator_feedback",
    "sla_amber_warning",
    "sla_red_critical_alert",
    "sla_breached",
    "kyc_submission_alert",
    "account_deletion_detection_alert",
    "mie_recommendation_alert",
    "mie_pipeline_alert",
}


def get_or_create_preference(*, user: User) -> NotificationPreference:
    """Lazily provision a NotificationPreference for `user` on first access."""

    preference, _created = NotificationPreference.objects.get_or_create(user=user)
    return preference


def update_preference(*, user: User, **fields) -> NotificationPreference:
    """Apply whichever preference toggles were provided (all optional)."""

    preference = get_or_create_preference(user=user)
    update_fields = ["updated_datetime"]

    for field, value in fields.items():
        if field in UPDATABLE_FIELDS and value is not None:
            setattr(preference, field, value)
            update_fields.append(field)

    if len(update_fields) > 1:
        preference.save(update_fields=update_fields)

    return preference

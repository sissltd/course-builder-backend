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
    "in_app_enabled",
}

#: Nullable fields where an explicit null is meaningful ("clear the override,
#: fall back to the platform default") rather than "field wasn't provided" -
#: so these are applied whenever the key is present at all, not only when
#: its value is not None (unlike UPDATABLE_FIELDS above).
NULLABLE_OVERRIDE_FIELDS = {
    "sla_amber_threshold_hours_override",
    "sla_red_threshold_hours_override",
}


def get_or_create_preference(*, user: User) -> NotificationPreference:
    """Lazily provision a NotificationPreference for `user` on first access."""

    preference, _created = NotificationPreference.objects.get_or_create(user=user)
    return preference


def update_preference(*, user: User, **fields) -> NotificationPreference:
    """Apply whichever preference toggles were provided (all optional).

    For the nullable SLA override fields, an explicit null clears the
    override (falls back to the platform default) - so those are applied
    whenever the key is present, not only when its value is truthy/non-None.
    """

    preference = get_or_create_preference(user=user)
    update_fields = ["updated_datetime"]

    for field, value in fields.items():
        if field in NULLABLE_OVERRIDE_FIELDS:
            setattr(preference, field, value)
            update_fields.append(field)
        elif field in UPDATABLE_FIELDS and value is not None:
            setattr(preference, field, value)
            update_fields.append(field)

    if len(update_fields) > 1:
        preference.save(update_fields=update_fields)

    return preference

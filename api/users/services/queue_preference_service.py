from api.users.models import QueueBehaviourPreference, User


def get_or_create_preference(*, user: User) -> QueueBehaviourPreference:
    """Lazily provision a QueueBehaviourPreference for `user` on first access.

    Mirrors reviewer_availability_service.get_or_create_availability - no
    row exists until a reviewer actually opens their Queue Behaviour settings.
    """

    preference, _created = QueueBehaviourPreference.objects.get_or_create(user=user)
    return preference


def update_preference(
    *,
    user: User,
    default_sort_order: str | None = None,
    auto_advance_enabled: bool | None = None,
    track_filter: str | None = None,
) -> QueueBehaviourPreference:
    """Apply whichever queue-behaviour fields were provided (all optional)."""

    preference = get_or_create_preference(user=user)
    update_fields = ["updated_datetime"]

    if default_sort_order is not None:
        preference.default_sort_order = default_sort_order
        update_fields.append("default_sort_order")
    if auto_advance_enabled is not None:
        preference.auto_advance_enabled = auto_advance_enabled
        update_fields.append("auto_advance_enabled")
    if track_filter is not None:
        preference.track_filter = track_filter
        update_fields.append("track_filter")

    if len(update_fields) > 1:
        preference.save(update_fields=list(dict.fromkeys(update_fields)))

    return preference

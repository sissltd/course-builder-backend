from rest_framework import exceptions

from api.users.models import ReviewerAvailability, User


def get_or_create_availability(*, user: User) -> ReviewerAvailability:
    """Lazily provision a ReviewerAvailability for `user` on first access.

    Mirrors creator_profile_service.get_or_create_profile - no row exists
    until a reviewer actually opens their Availability settings.
    """

    availability, _created = ReviewerAvailability.objects.get_or_create(user=user)
    return availability


def update_availability(
    *,
    user: User,
    is_available: bool | None = None,
    unavailability_reason: str | None = None,
    return_date=None,
    auto_return_enabled: bool | None = None,
) -> ReviewerAvailability:
    """Apply whichever availability fields were provided (all optional).

    Setting is_available=True clears the reason/return_date, since they're
    only meaningful while unavailable.
    """

    availability = get_or_create_availability(user=user)
    update_fields = ["updated_datetime"]

    if is_available is not None:
        availability.is_available = is_available
        update_fields.append("is_available")
        if is_available:
            availability.unavailability_reason = ""
            availability.return_date = None
            update_fields += ["unavailability_reason", "return_date"]
    if unavailability_reason is not None:
        availability.unavailability_reason = unavailability_reason
        update_fields.append("unavailability_reason")
    if return_date is not None:
        availability.return_date = return_date
        update_fields.append("return_date")
    if auto_return_enabled is not None:
        availability.auto_return_enabled = auto_return_enabled
        update_fields.append("auto_return_enabled")

    if len(update_fields) > 1:
        availability.save(update_fields=list(dict.fromkeys(update_fields)))

    return availability


def is_reviewer_available(*, user: User) -> bool:
    """Whether `user` can claim/approve/reject courses right now.

    Defaults to True when no ReviewerAvailability row exists yet (a reviewer
    who's never touched their Availability settings is available).
    """

    availability = ReviewerAvailability.objects.filter(user=user).first()
    return availability is None or availability.is_effectively_available


def require_reviewer_available(*, user: User) -> None:
    """Raise ValidationError if `user` is currently marked Unavailable."""

    if not is_reviewer_available(user=user):
        raise exceptions.ValidationError(
            "You are marked Unavailable and cannot claim, approve, or reject courses right now."
        )

from django.utils import timezone

from api.onboarding.models import CreatorProfile
from api.users.enums import UserActivityActionEnums
from api.users.models import User


def get_or_create_profile(*, user: User) -> CreatorProfile:
    """Lazily provision a CreatorProfile for `user` on first access.

    Mirrors wallet_service.get_or_create_wallet - no row exists until a user
    actually starts the onboarding wizard.
    """

    profile, _created = CreatorProfile.objects.get_or_create(user=user)
    return profile


def update_profile(
    *,
    user: User,
    category_id=None,
    expertise_area: str | None = None,
    other_expertise: str | None = None,
    video_comfort_level: str | None = None,
    monthly_course_capacity: str | None = None,
    agreement_accepted: bool | None = None,
) -> CreatorProfile:
    """Apply whichever onboarding-step fields were provided (all optional).

    Only touches fields actually passed in, so the frontend can call this
    once per wizard step with a partial payload and resume a dropped-off
    wizard freely. Setting agreement_accepted=True stamps both
    agreement_accepted_at and onboarding_completed_at, since the agreement
    step is the final step of the wizard, and logs a UserActivityLog entry.
    """

    profile = get_or_create_profile(user=user)
    update_fields = ["updated_datetime"]

    if category_id is not None:
        profile.primary_expertise_category_id = category_id
        update_fields.append("primary_expertise_category")
    if expertise_area is not None:
        profile.primary_expertise_area = expertise_area
        update_fields.append("primary_expertise_area")
    if other_expertise is not None:
        profile.primary_expertise_other = other_expertise
        update_fields.append("primary_expertise_other")
    if video_comfort_level is not None:
        profile.video_comfort_level = video_comfort_level
        update_fields.append("video_comfort_level")
    if monthly_course_capacity is not None:
        profile.monthly_course_capacity = monthly_course_capacity
        update_fields.append("monthly_course_capacity")
    if agreement_accepted:
        now = timezone.now()
        profile.agreement_accepted_at = now
        profile.onboarding_completed_at = now
        update_fields += ["agreement_accepted_at", "onboarding_completed_at"]

    if len(update_fields) > 1:
        profile.save(update_fields=update_fields)

    if agreement_accepted:
        # Local import: keeps api.onboarding -> api.authentication a one-directional
        # call at runtime rather than a module-level import coupling.
        from api.authentication.services import activity_service

        activity_service.log_auth_activity(
            user=user,
            action=UserActivityActionEnums.ONBOARDING_COMPLETED,
            summary="Creator completed onboarding.",
        )

    return profile

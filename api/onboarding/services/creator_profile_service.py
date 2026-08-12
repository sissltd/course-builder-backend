from django.utils import timezone

from api.onboarding.models import CreatorProfile
from api.users.enums import UserActivityActionEnums, UserActivityCategoryEnums
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
    request=None,
) -> CreatorProfile:
    """Apply whichever onboarding-step fields were provided (all optional).

    Only touches fields actually passed in, so the frontend can call this
    once per wizard step with a partial payload and resume a dropped-off
    wizard freely. Every call is logged under UserActivityCategoryEnums.
    ONBOARDING so the whole wizard is traceable as one group:

    - Any expertise/proficiency field(s) supplied -> one ONBOARDING_STEP_UPDATED
      entry per call (not one per field), naming which fields changed.
    - agreement_accepted=True -> always stamps agreement_accepted_at and the
      currently-effective platform policy version, and logs POLICY_ACCEPTED.
      This covers both first-time acceptance and a later re-acceptance after
      an Admin bumps PlatformSettings.creator_agreement_policy_version.
    - onboarding_completed_at (and ONBOARDING_COMPLETED /
      COURSE_BUILDER_ACCESS_GRANTED) are only ever stamped/logged the FIRST
      time - a re-acceptance after a policy bump must not look like the
      creator "completed onboarding" again, and must not reset when they
      originally finished.
    """

    profile = get_or_create_profile(user=user)
    update_fields = ["updated_datetime"]
    updated_step_labels = []

    if category_id is not None:
        profile.primary_expertise_category_id = category_id
        update_fields.append("primary_expertise_category")
        updated_step_labels.append("category_id")
    if expertise_area is not None:
        profile.primary_expertise_area = expertise_area
        update_fields.append("primary_expertise_area")
        updated_step_labels.append("expertise_area")
    if other_expertise is not None:
        profile.primary_expertise_other = other_expertise
        update_fields.append("primary_expertise_other")
        updated_step_labels.append("other_expertise")
    if video_comfort_level is not None:
        profile.video_comfort_level = video_comfort_level
        update_fields.append("video_comfort_level")
        updated_step_labels.append("video_comfort_level")
    if monthly_course_capacity is not None:
        profile.monthly_course_capacity = monthly_course_capacity
        update_fields.append("monthly_course_capacity")
        updated_step_labels.append("monthly_course_capacity")

    is_first_completion = False
    if agreement_accepted:
        # Local import: keeps api.onboarding -> api.platform a one-directional
        # call at runtime rather than a module-level import coupling.
        from api.platform.services import platform_settings_service

        now = timezone.now()
        current_version = (
            platform_settings_service.get_settings().creator_agreement_policy_version
        )
        profile.agreement_accepted_at = now
        profile.agreement_accepted_version = current_version
        update_fields += ["agreement_accepted_at", "agreement_accepted_version"]
        if profile.onboarding_completed_at is None:
            is_first_completion = True
            profile.onboarding_completed_at = now
            update_fields.append("onboarding_completed_at")

    if len(update_fields) > 1:
        profile.save(update_fields=update_fields)

    # Local import: keeps api.onboarding -> api.authentication a
    # one-directional call at runtime rather than a module-level coupling.
    from api.authentication.services import activity_service

    if updated_step_labels:
        activity_service.log_activity(
            user=user,
            category=UserActivityCategoryEnums.ONBOARDING,
            action=UserActivityActionEnums.ONBOARDING_STEP_UPDATED,
            summary=f"Updated onboarding fields: {', '.join(updated_step_labels)}.",
            details={"updated_fields": updated_step_labels},
            request=request,
        )

    if agreement_accepted:
        activity_service.log_activity(
            user=user,
            category=UserActivityCategoryEnums.ONBOARDING,
            action=UserActivityActionEnums.POLICY_ACCEPTED,
            summary="Creator accepted the creator agreement.",
            details={"policy_version": profile.agreement_accepted_version},
            request=request,
        )
        if is_first_completion:
            activity_service.log_activity(
                user=user,
                category=UserActivityCategoryEnums.ONBOARDING,
                action=UserActivityActionEnums.ONBOARDING_COMPLETED,
                summary="Creator completed onboarding.",
                details={
                    "primary_expertise_area": profile.primary_expertise_area,
                    "monthly_course_capacity": profile.monthly_course_capacity,
                },
                request=request,
            )
            activity_service.log_activity(
                user=user,
                category=UserActivityCategoryEnums.ONBOARDING,
                action=UserActivityActionEnums.COURSE_BUILDER_ACCESS_GRANTED,
                summary="Course Builder access granted after completing onboarding.",
                request=request,
            )

    # Transient, non-persisted flag so the view can tell a first-time
    # completion (issue tokens) apart from a later re-acceptance (don't)
    # without re-deriving it from timestamp comparisons.
    profile.is_first_completion = is_first_completion

    return profile

from django.db import models
from django.utils.translation import gettext_lazy as _

from api.onboarding.enums import MonthlyCourseCapacity, VideoComfortLevel
from includes.helpers import DateHistoryModelMixin, UUIDPrimaryKeyModelMixin


class CreatorProfile(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """Onboarding-collected profile data for a Course Creator.

    Lazily provisioned via creator_profile_service.get_or_create_profile,
    mirroring wallet_service.get_or_create_wallet - no row exists until the
    user starts the onboarding wizard. agreement_accepted_at is the
    account-level NDA / content-ownership agreement collected once during
    onboarding, kept separate from (and in addition to) Course.terms_accepted_at
    which is still required per-course at submission time (BR-005).
    """

    user = models.OneToOneField(
        "users.User",
        verbose_name=_("User"),
        on_delete=models.CASCADE,
        related_name="creator_profile",
        help_text=_("User this onboarding profile belongs to."),
    )
    primary_expertise_category = models.ForeignKey(
        "courses.Category",
        verbose_name=_("Primary Expertise Category"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text=_("Primary area-of-expertise category selected during onboarding."),
    )
    primary_expertise_other = models.CharField(
        verbose_name=_("Primary Expertise (Other)"),
        max_length=255,
        blank=True,
        default="",
        help_text=_("Free-text expertise when 'Others (Specify)' is chosen instead of a category."),
    )
    video_comfort_level = models.CharField(
        verbose_name=_("Video Comfort Level"),
        max_length=30,
        choices=VideoComfortLevel.choices,
        blank=True,
        default="",
        help_text=_("Self-reported comfort producing video content."),
    )
    monthly_course_capacity = models.CharField(
        verbose_name=_("Monthly Course Capacity"),
        max_length=20,
        choices=MonthlyCourseCapacity.choices,
        blank=True,
        default="",
        help_text=_("Self-estimated number of courses producible per month."),
    )
    agreement_accepted_at = models.DateTimeField(
        verbose_name=_("Agreement Accepted At"),
        null=True,
        blank=True,
        help_text=_("When the creator accepted the account-level NDA / content-ownership agreement."),
    )
    onboarding_completed_at = models.DateTimeField(
        verbose_name=_("Onboarding Completed At"),
        null=True,
        blank=True,
        help_text=_("Set when the final onboarding step (agreement) is submitted."),
    )

    class Meta:
        verbose_name = _("Creator Profile")
        verbose_name_plural = _("Creator Profiles")
        indexes = [
            models.Index(fields=["onboarding_completed_at"], name="creatorprofile_completed_idx"),
        ]

    @property
    def has_completed_onboarding(self) -> bool:
        return self.onboarding_completed_at is not None

    def __str__(self):
        """Use the owning user's id as the human-readable label."""

        return f"CreatorProfile({self.user_id})"

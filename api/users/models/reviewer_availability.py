from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from api.users.enums import UnavailabilityReason
from includes.helpers import DateHistoryModelMixin, UUIDPrimaryKeyModelMixin


class ReviewerAvailability(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """A Creator Reviewer's availability for new review-queue actions.

    Lazily provisioned via reviewer_availability_service.get_or_create,
    mirroring onboarding's CreatorProfile - no row exists until a reviewer
    first opens their Availability settings. "Auto return on date" is
    computed on read (return_date in the past => available) rather than
    flipped by a scheduled job, so there's no Celery Beat task to maintain.
    """

    user = models.OneToOneField(
        "users.User",
        verbose_name=_("User"),
        on_delete=models.CASCADE,
        related_name="reviewer_availability",
        help_text=_("Reviewer this availability setting belongs to."),
    )
    is_available = models.BooleanField(
        verbose_name=_("Is Available"),
        default=True,
        help_text=_("Whether new courses can be claimed/approved/rejected by this reviewer."),
    )
    unavailability_reason = models.CharField(
        verbose_name=_("Unavailability Reason"),
        max_length=20,
        choices=UnavailabilityReason.choices,
        blank=True,
        default="",
        help_text=_("Why this reviewer is unavailable, if applicable."),
    )
    return_date = models.DateField(
        verbose_name=_("Return Date"),
        null=True,
        blank=True,
        help_text=_("Date this reviewer expects to become available again."),
    )
    auto_return_enabled = models.BooleanField(
        verbose_name=_("Auto Return Enabled"),
        default=True,
        help_text=_("Automatically treat as available once return_date has passed."),
    )

    class Meta:
        verbose_name = _("Reviewer Availability")
        verbose_name_plural = _("Reviewer Availabilities")

    @property
    def is_effectively_available(self) -> bool:
        """is_available, or auto-returned because return_date has passed."""

        if self.is_available:
            return True
        if self.auto_return_enabled and self.return_date:
            return self.return_date <= timezone.localdate()
        return False

    def __str__(self):
        """Use the owning user's id as the human-readable label."""

        return f"ReviewerAvailability({self.user_id})"

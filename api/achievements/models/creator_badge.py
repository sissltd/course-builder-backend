from django.db import models
from django.utils.translation import gettext_lazy as _

from api.achievements.enums import AwardSource
from core.mixins import (
    DateHistoryModelMixin,
    SoftDeleteModelMixin,
    UUIDPrimaryKeyModelMixin,
)


class CreatorBadge(
    UUIDPrimaryKeyModelMixin, DateHistoryModelMixin, SoftDeleteModelMixin
):
    """One badge held by one creator.

    Soft-deleted when revoked or when its badge is deleted, so the history of
    who held what survives. A creator holds a given badge at most once at a
    time.
    """

    badge = models.ForeignKey(
        "achievements.Badge",
        verbose_name=_("Badge"),
        on_delete=models.CASCADE,
        related_name="awards",
        help_text=_("The badge awarded."),
    )
    creator = models.ForeignKey(
        "users.User",
        verbose_name=_("Creator"),
        on_delete=models.CASCADE,
        related_name="badge_awards",
        help_text=_("The creator holding the badge."),
    )
    awarded_by = models.ForeignKey(
        "users.User",
        verbose_name=_("Awarded by"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="badges_awarded",
        help_text=_("Staff member who awarded it by hand; null when automatic."),
    )
    source = models.CharField(
        verbose_name=_("Source"),
        max_length=20,
        choices=AwardSource.choices,
        help_text=_("How the creator came to hold the badge."),
    )
    awarded_at = models.DateTimeField(
        verbose_name=_("Awarded at"),
        help_text=_("When the badge was awarded."),
    )

    class Meta:
        verbose_name = _("Creator badge")
        verbose_name_plural = _("Creator badges")
        ordering = ["-awarded_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["badge", "creator"],
                condition=models.Q(is_deleted=False),
                name="creator_badge_unique_live_award",
            ),
        ]
        indexes = [
            models.Index(
                fields=["creator", "is_deleted"], name="creator_badge_creator_idx"
            ),
            models.Index(
                fields=["badge", "is_deleted"], name="creator_badge_badge_idx"
            ),
        ]

    def __str__(self):
        return f"{self.creator_id} holds {self.badge_id}"

from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from api.categories.enums import CategoryStatus, TrackPreference
from includes.helpers import (
    DateHistoryModelMixin,
    UUIDPrimaryKeyModelMixin,
    UserHistoryModelMixin,
)


class Category(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin, UserHistoryModelMixin):
    """A staff-managed course category with fixed creator pricing.

    Price changes are not retroactive: Course.creator_price_snapshot captures
    the price in effect when a course is submitted, so editing a category's
    price here only affects courses submitted after the change.
    """

    name = models.CharField(
        verbose_name=_("Name"),
        max_length=150,
        unique=True,
        help_text=_("Category display name."),
    )
    description = models.TextField(
        verbose_name=_("Description"),
        blank=True,
        default="",
        help_text=_("Description of the category shown to creators."),
    )
    creator_price = models.DecimalField(
        verbose_name=_("Creator Price"),
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
        help_text=_(
            "Fixed price paid to a creator for an approved course in this category."
        ),
    )
    track_preference = models.CharField(
        verbose_name=_("Track Preference"),
        max_length=20,
        choices=TrackPreference.choices,
        default=TrackPreference.OPEN,
        help_text=_("Which production track this category is best suited for."),
    )
    status = models.CharField(
        verbose_name=_("Status"),
        max_length=10,
        choices=CategoryStatus.choices,
        default=CategoryStatus.ACTIVE,
        help_text=_("Whether the category currently accepts new course submissions."),
    )

    class Meta:
        verbose_name = _("Category")
        verbose_name_plural = _("Categories")
        ordering = ["name"]
        indexes = [
            models.Index(
                fields=["status", "track_preference"],
                name="category_status_track_idx",
            ),
        ]

    def __str__(self):
        """Use the category name as the human-readable label."""

        return self.name

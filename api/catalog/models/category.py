from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from django.utils.text import slugify

from api.catalog.enums import CategoryStatus, TrackPreference
from core.mixins import (
    DateHistoryModelMixin,
    UserHistoryModelMixin,
    UUIDPrimaryKeyModelMixin,
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
    slug = models.SlugField(
        verbose_name=_("Slug"),
        max_length=160,
        unique=True,
        blank=True,
        help_text=_(
            "URL-safe identifier derived from the name when omitted. Used for "
            "lookups and discovery."
        ),
    )
    description = models.TextField(
        verbose_name=_("Description"),
        blank=True,
        default="",
        help_text=_("Description of the category shown to creators."),
    )
    creator_price_beginner = models.DecimalField(
        verbose_name=_("Creator Price - Beginner"),
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal(0))],
        help_text=_("Payout for an approved Beginner course in this category."),
    )
    creator_price_intermediate = models.DecimalField(
        verbose_name=_("Creator Price - Intermediate"),
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal(0))],
        help_text=_("Payout for an approved Intermediate course in this category."),
    )
    creator_price_advanced = models.DecimalField(
        verbose_name=_("Creator Price - Advanced"),
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal(0))],
        help_text=_("Payout for an approved Advanced course in this category."),
    )
    icon = models.CharField(
        verbose_name=_("Icon"),
        max_length=50,
        blank=True,
        default="",
        help_text=_(
            "Icon identifier shown beside the category in the picker. Free "
            "text so the client owns its own icon set rather than the API "
            "constraining it to one library."
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

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)[:160]
        super().save(*args, **kwargs)

    def price_for(self, difficulty_level) -> Decimal:
        """Payout for a course of `difficulty_level` in this category.

        The three levels key off courses.DifficultyLevel. An unrecognised
        or missing level falls back to the beginner rate rather than
        raising: a payout must always resolve to a number, and the entry
        rate is the safe direction to err in.
        """

        return {
            "BEGINNER": self.creator_price_beginner,
            "INTERMEDIATE": self.creator_price_intermediate,
            "ADVANCED": self.creator_price_advanced,
        }.get(difficulty_level, self.creator_price_beginner)

    def __str__(self):
        """Use the category name as the human-readable label."""

        return self.name

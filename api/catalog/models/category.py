from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from django.utils.text import slugify

from api.catalog.enums import CategoryStatus, CreatorPriceType, TrackPreference
from core.mixins import (
    DateHistoryModelMixin,
    SoftDeleteModelMixin,
    UserHistoryModelMixin,
    UUIDPrimaryKeyModelMixin,
)


class Category(
    UUIDPrimaryKeyModelMixin,
    DateHistoryModelMixin,
    SoftDeleteModelMixin,
    UserHistoryModelMixin,
):
    """A staff-managed course category with fixed creator pricing.

    Three prices, one per CreatorPriceType. Only holders of
    `catalog.set_creator_prices` (the Writer, Admin, Super Admin) set them.
    Price changes are not retroactive: a course locks the category's (or its
    topic's) prices when it is submitted (Course.creator_rates_snapshot), so
    editing them here only affects courses submitted afterwards.
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
    creator_price_without_video = models.DecimalField(
        verbose_name=_("Creator Price - Without Video"),
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal(0))],
        help_text=_(
            "Paid to a creator who writes the course and leaves its video to "
            "the Production Engine. Paid when Second Review approves it."
        ),
    )
    creator_price_with_video = models.DecimalField(
        verbose_name=_("Creator Price - With Video"),
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal(0))],
        help_text=_(
            "Paid to a creator who writes the course and adds its video, and "
            "to a paid developer. Paid when QA approves it."
        ),
    )
    creator_price_ai_built = models.DecimalField(
        verbose_name=_("Creator Price - AI-built"),
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal(0))],
        help_text=_(
            "Paid to a creator who builds the course with our AI. Paid at "
            "Second Review or QA, by who supplies the video."
        ),
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

    def price_for(self, price_type: str) -> Decimal:
        """This category's creator price of `price_type` (CreatorPriceType)."""

        return {
            CreatorPriceType.WITHOUT_VIDEO: self.creator_price_without_video,
            CreatorPriceType.WITH_VIDEO: self.creator_price_with_video,
            CreatorPriceType.AI_BUILT: self.creator_price_ai_built,
        }[price_type]

    def __str__(self):
        """Use the category name as the human-readable label."""

        return self.name

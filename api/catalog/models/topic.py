from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from api.catalog.enums import CategoryStatus, CreatorPriceType
from core.mixins import (
    DateHistoryModelMixin,
    UserHistoryModelMixin,
    UUIDPrimaryKeyModelMixin,
)


class Topic(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin, UserHistoryModelMixin):
    """An Admin-managed course topic nested under a Category.

    A topic may override any of its category's three creator prices; a
    price left blank falls back to the category's of the same type
    (`price_for`). Same rules as Category's prices (non-retroactive, set
    only by holders of `catalog.set_creator_prices`).
    """

    category = models.ForeignKey(
        "catalog.Category",
        verbose_name=_("Category"),
        on_delete=models.CASCADE,
        related_name="topics",
        help_text=_("Category this topic belongs to."),
    )
    name = models.CharField(
        verbose_name=_("Name"),
        max_length=150,
        help_text=_("Topic display name."),
    )
    slug = models.SlugField(
        verbose_name=_("Slug"),
        max_length=160,
        blank=True,
        help_text=_(
            "URL-safe identifier derived from the name when omitted. Unique "
            "within its category."
        ),
    )
    creator_price_without_video = models.DecimalField(
        verbose_name=_("Creator Price - Without Video"),
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal(0))],
        help_text=_(
            "This topic's own without-video price. Blank uses the category's."
        ),
    )
    creator_price_with_video = models.DecimalField(
        verbose_name=_("Creator Price - With Video"),
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal(0))],
        help_text=_(
            "This topic's own with-video price. Blank uses the category's."
        ),
    )
    creator_price_ai_built = models.DecimalField(
        verbose_name=_("Creator Price - AI-built"),
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal(0))],
        help_text=_(
            "This topic's own AI-built price. Blank uses the category's."
        ),
    )
    status = models.CharField(
        verbose_name=_("Status"),
        max_length=10,
        choices=CategoryStatus.choices,
        default=CategoryStatus.ACTIVE,
        help_text=_("Whether the topic currently accepts new course submissions."),
    )
    reserved_by = models.ForeignKey(
        "users.User",
        verbose_name=_("Reserved By"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text=_(
            "Creator this topic is currently reserved for (PRD BR-007), if any."
        ),
    )
    reserved_until = models.DateField(
        verbose_name=_("Reserved Until"),
        null=True,
        blank=True,
        help_text=_("Date the current reservation expires."),
    )

    class Meta:
        verbose_name = _("Topic")
        verbose_name_plural = _("Topics")
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["category", "name"], name="unique_topic_name_per_category"
            ),
            models.UniqueConstraint(
                fields=["category", "slug"], name="unique_topic_slug_per_category"
            ),
        ]
        indexes = [
            models.Index(
                fields=["category", "status"], name="topic_category_status_idx"
            ),
        ]

    def price_for(self, price_type: str) -> Decimal:
        """This topic's creator price of `price_type`, or its category's when
        the topic does not override it."""

        own = {
            CreatorPriceType.WITHOUT_VIDEO: self.creator_price_without_video,
            CreatorPriceType.WITH_VIDEO: self.creator_price_with_video,
            CreatorPriceType.AI_BUILT: self.creator_price_ai_built,
        }[price_type]
        return own if own is not None else self.category.price_for(price_type)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)[:160]
        super().save(*args, **kwargs)

    @property
    def is_currently_reserved(self) -> bool:
        """Whether this topic has an active (non-expired) reservation.

        Computed on read rather than flipped by a scheduled job - same idiom
        as ReviewerAvailability.is_effectively_available.
        """

        return bool(self.reserved_until and self.reserved_until >= timezone.localdate())

    def __str__(self):
        """Use the topic name as the human-readable label."""

        return self.name

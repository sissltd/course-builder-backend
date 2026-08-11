from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from api.categories.enums import CategoryStatus
from core.mixins import (
    DateHistoryModelMixin,
    UserHistoryModelMixin,
    UUIDPrimaryKeyModelMixin,
)


class Topic(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin, UserHistoryModelMixin):
    """An Admin-managed course topic nested under a Category, with its own price.

    Mirrors Category's pricing model (fixed, non-retroactive - see
    Category's docstring) but scoped one level down: a Course's price is
    snapshotted from its Topic when one is set, falling back to the
    Category's price otherwise (course_service.submit_course).
    """

    category = models.ForeignKey(
        "categories.Category",
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
    creator_price = models.DecimalField(
        verbose_name=_("Creator Price"),
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal(0))],
        help_text=_(
            "Fixed price paid to a creator for an approved course in this topic."
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
        ]
        indexes = [
            models.Index(
                fields=["category", "status"], name="topic_category_status_idx"
            ),
        ]

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

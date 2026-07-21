from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from api.categories.enums import CategoryStatus
from includes.helpers import (
    DateHistoryModelMixin,
    UUIDPrimaryKeyModelMixin,
    UserHistoryModelMixin,
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
        validators=[MinValueValidator(Decimal("0"))],
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

    def __str__(self):
        """Use the topic name as the human-readable label."""

        return self.name

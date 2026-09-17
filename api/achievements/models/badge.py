from django.core.validators import MinValueValidator
from django.db import models
from django.db.models.functions import Lower
from django.utils.translation import gettext_lazy as _

from api.achievements.enums import (
    CRITERION_REQUIREMENT_PHRASES,
    BadgeCriterion,
)
from core.mixins import (
    DateHistoryModelMixin,
    SoftDeleteModelMixin,
    UserHistoryModelMixin,
    UUIDPrimaryKeyModelMixin,
)


class Badge(
    UUIDPrimaryKeyModelMixin,
    DateHistoryModelMixin,
    SoftDeleteModelMixin,
    UserHistoryModelMixin,
):
    """An achievement staff define and course creators earn.

    A creator qualifies once their count for `criterion` reaches
    `required_count`. Badges sharing a criterion form a ladder ordered by
    `required_count`, which is what "the previous badge" means when one is
    deleted - hence the uniqueness of (criterion, required_count).
    """

    title = models.CharField(
        verbose_name=_("Title"),
        max_length=80,
        help_text=_("Badge name shown to staff and creators, e.g. 'Top'."),
    )
    icon = models.CharField(
        verbose_name=_("Icon"),
        max_length=50,
        help_text=_(
            "Icon identifier from the client's badge icon set. Free text so the "
            "client owns its icons, as with Category.icon."
        ),
    )
    color = models.CharField(
        verbose_name=_("Color"),
        max_length=7,
        help_text=_("Badge colour as a #RRGGBB hex string."),
    )
    criterion = models.CharField(
        verbose_name=_("Criterion"),
        max_length=30,
        choices=BadgeCriterion.choices,
        default=BadgeCriterion.COURSES_CREATED,
        help_text=_("What `required_count` counts for each creator."),
    )
    required_count = models.PositiveIntegerField(
        verbose_name=_("Required count"),
        validators=[MinValueValidator(1)],
        help_text=_("How many courses meeting the criterion earn this badge."),
    )
    auto_award = models.BooleanField(
        verbose_name=_("Auto award"),
        default=False,
        help_text=_(
            "Award automatically when a creator meets the requirement. Off "
            "means staff award it by hand."
        ),
    )

    class Meta:
        verbose_name = _("Badge")
        verbose_name_plural = _("Badges")
        ordering = ["criterion", "-required_count"]
        constraints = [
            models.UniqueConstraint(
                Lower("title"),
                condition=models.Q(is_deleted=False),
                name="badge_unique_live_title",
            ),
            models.UniqueConstraint(
                fields=["criterion", "required_count"],
                condition=models.Q(is_deleted=False),
                name="badge_unique_live_criterion_count",
            ),
            models.CheckConstraint(
                condition=models.Q(required_count__gte=1),
                name="badge_required_count_positive",
            ),
        ]
        indexes = [
            models.Index(
                fields=["criterion", "required_count", "is_deleted"],
                name="badge_criterion_count_idx",
            ),
        ]

    def __str__(self):
        return self.title

    @property
    def requirement_summary(self) -> str:
        noun = "course" if self.required_count == 1 else "courses"
        phrase = CRITERION_REQUIREMENT_PHRASES[self.criterion].format(
            count=self.required_count, noun=noun
        )
        return f"For creators who have {phrase}"

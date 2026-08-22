from django.db import models
from django.utils.translation import gettext_lazy as _

from core.mixins import (
    DateHistoryModelMixin,
    UUIDPrimaryKeyModelMixin,
    UserHistoryModelMixin,
)


class CourseVersion(
    UUIDPrimaryKeyModelMixin, DateHistoryModelMixin, UserHistoryModelMixin
):
    """A canonical course version label shared by all published courses.

    The system ships with the single "1.0" row pre-seeded. Each course that
    is published links its PublishedCourseSnapshot to this record instead of
    storing its own free-text version string, so the label stays consistent
    across courses and can be extended with "2.0" etc. without a schema
    change (SCCS PRD Section 15).
    """

    label = models.CharField(
        verbose_name=_("Label"),
        max_length=10,
        unique=True,
        help_text=_("Canonical version label, e.g. '1.0'."),
    )
    is_active = models.BooleanField(
        verbose_name=_("Active"),
        default=True,
        help_text=_(
            "Whether new course publications may use this version label. "
            "Set to False to freeze an old version."
        ),
    )

    class Meta:
        verbose_name = _("Course Version")
        verbose_name_plural = _("Course Versions")
        ordering = ["label"]

    def __str__(self):
        """Use the canonical label as the human-readable name."""

        return self.label

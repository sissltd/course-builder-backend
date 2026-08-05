from django.db import models
from django.utils.translation import gettext_lazy as _

from includes.helpers import (
    DateHistoryModelMixin,
    UUIDPrimaryKeyModelMixin,
    UserHistoryModelMixin,
)


class CourseVersion(
    UUIDPrimaryKeyModelMixin, DateHistoryModelMixin, UserHistoryModelMixin
):
    """An immutable snapshot of a Course at the moment it was published
    (SCCS PRD Section 15).

    Currently only ever created once per course, in course_service.publish_
    course - there is no re-edit-after-publish workflow yet (publishing is a
    one-way transition with no unpublish action), so a course cannot acquire
    a second version through this API today. The model/field exist so that
    workflow can be layered on later without a schema change.
    """

    course = models.ForeignKey(
        "courses.Course",
        verbose_name=_("Course"),
        on_delete=models.CASCADE,
        related_name="versions",
        help_text=_("Course this version snapshot belongs to."),
    )
    version_number = models.CharField(
        verbose_name=_("Version Number"),
        max_length=10,
        help_text=_("Semantic version at publish time, e.g. '1.0'."),
    )
    published_at = models.DateTimeField(
        verbose_name=_("Published At"),
        help_text=_("When this version was published."),
    )
    snapshot = models.JSONField(
        verbose_name=_("Snapshot"),
        help_text=_("Course/module/lesson tree as it existed at publish time."),
    )

    class Meta:
        verbose_name = _("Course Version")
        verbose_name_plural = _("Course Versions")
        ordering = ["-published_at"]

    def __str__(self):
        """Identify which course/version this snapshot represents."""

        return f"{self.course_id} v{self.version_number}"

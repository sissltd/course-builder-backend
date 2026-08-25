from django.db import models
from django.utils.translation import gettext_lazy as _

from core.mixins import DateHistoryModelMixin, UUIDPrimaryKeyModelMixin


class SubmissionRejectionReason(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """Admin-managed taxonomy backing dedup check #1.

    When an incoming idea's title matches a previously rejected reason,
    the submission is short-circuited to PREVIOUSLY_REJECTED without ever
    reaching the queue. Reasons are soft-deactivated (is_active=False)
    rather than deleted so historical submissions keep pointing at them.
    """

    label = models.CharField(
        verbose_name=_("Label"),
        max_length=255,
        unique=True,
        help_text=_("Short reason shown to admins; matched against past rejections."),
    )
    description = models.TextField(
        verbose_name=_("Description"),
        blank=True,
        help_text=_("Longer explanation of when this reason applies."),
    )
    is_active = models.BooleanField(
        verbose_name=_("Is Active"),
        default=True,
        help_text=_("Inactive reasons stay on historical rows but stop matching."),
    )

    class Meta:
        verbose_name = _("Submission Rejection Reason")
        verbose_name_plural = _("Submission Rejection Reasons")
        ordering = ["label"]

    def __str__(self):
        return self.label

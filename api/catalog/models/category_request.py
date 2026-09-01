from django.db import models
from django.utils.translation import gettext_lazy as _

from api.catalog.enums import CategoryRequestStatus
from core.mixins import DateHistoryModelMixin, UUIDPrimaryKeyModelMixin


class CategoryRequest(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """A creator's request for a new Category that doesn't exist yet.

    Approving a request creates a real Category (resulting_category) and
    emails the requester; rejecting just closes the request out. Kept
    separate from Category itself so an unapproved request never behaves
    like a usable category.
    """

    requested_by = models.ForeignKey(
        "users.User",
        verbose_name=_("Requested By"),
        on_delete=models.CASCADE,
        related_name="category_requests",
        help_text=_("Creator who requested this category."),
    )
    name = models.CharField(
        verbose_name=_("Name"),
        max_length=150,
        help_text=_("Requested category name."),
    )
    description = models.TextField(
        verbose_name=_("Description"),
        blank=True,
        default="",
        help_text=_(
            "Why this category is needed and what belongs in it. Carried "
            "onto the Category created on approval."
        ),
    )
    status = models.CharField(
        verbose_name=_("Status"),
        max_length=10,
        choices=CategoryRequestStatus.choices,
        default=CategoryRequestStatus.PENDING,
        help_text=_("Whether this request is pending, approved, or rejected."),
    )
    resulting_category = models.ForeignKey(
        "catalog.Category",
        verbose_name=_("Resulting Category"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text=_("The Category created when this request was approved."),
    )
    reviewed_by = models.ForeignKey(
        "users.User",
        verbose_name=_("Reviewed By"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text=_("Admin who approved or rejected this request."),
    )
    reviewed_at = models.DateTimeField(
        verbose_name=_("Reviewed At"), null=True, blank=True
    )

    class Meta:
        verbose_name = _("Category Request")
        verbose_name_plural = _("Category Requests")
        ordering = ["-created_datetime"]
        indexes = [
            models.Index(fields=["status"], name="category_request_status_idx"),
        ]

    def __str__(self):
        """Use the requested name as the human-readable label."""

        return self.name

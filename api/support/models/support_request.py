from django.db import models
from django.utils.translation import gettext_lazy as _

from api.support.enums import SupportRequestKind, SupportRequestStatus
from core.mixins import DateHistoryModelMixin, UUIDPrimaryKeyModelMixin


class SupportRequest(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """One submission from the Support page: a contact message, a ticket, or
    a request for an appeal (PRD Section 12 / business rules: creators can
    dispute a rejection or a suspension in writing; the decision is final).

    The three actions share one table because their forms are the same shape
    (who, how to reach them, what happened) and staff review them in one
    queue. Contact messages may come from anonymous visitors, so
    `submitted_by` is nullable.
    """

    kind = models.CharField(
        verbose_name=_("Kind"), max_length=10, choices=SupportRequestKind.choices
    )
    submitted_by = models.ForeignKey(
        "users.User",
        verbose_name=_("Submitted By"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_requests",
        help_text=_("Signed-in submitter; empty for anonymous contact messages."),
    )
    first_name = models.CharField(
        verbose_name=_("First Name"), max_length=150, blank=True
    )
    last_name = models.CharField(
        verbose_name=_("Last Name"), max_length=150, blank=True
    )
    email = models.EmailField(
        verbose_name=_("Email"),
        help_text=_("Contact email as typed on the form."),
    )
    country = models.CharField(
        verbose_name=_("Country"),
        max_length=2,
        blank=True,
        help_text=_("ISO 3166-1 alpha-2 country code (contact form)."),
    )
    title = models.CharField(verbose_name=_("Title"), max_length=255, blank=True)
    web_link = models.URLField(
        verbose_name=_("Web Link"), blank=True, help_text=_("Optional supporting link.")
    )
    message = models.TextField(
        verbose_name=_("Message"),
        help_text=_("Contact message, or the ticket/appeal description."),
    )
    status = models.CharField(
        verbose_name=_("Status"),
        max_length=10,
        choices=SupportRequestStatus.choices,
        default=SupportRequestStatus.OPEN,
    )
    due_at = models.DateTimeField(
        verbose_name=_("Due At"),
        null=True,
        blank=True,
        help_text=_("Appeals only: PRD dispute-resolution SLA (7 business days)."),
    )
    resolution_notes = models.TextField(verbose_name=_("Resolution Notes"), blank=True)
    resolved_by = models.ForeignKey(
        "users.User",
        verbose_name=_("Resolved By"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    resolved_at = models.DateTimeField(
        verbose_name=_("Resolved At"), null=True, blank=True
    )

    class Meta:
        verbose_name = _("Support Request")
        verbose_name_plural = _("Support Requests")
        ordering = ["-created_datetime"]
        indexes = [
            models.Index(fields=["kind", "status"], name="support_kind_status_idx"),
        ]

    def __str__(self):
        """Summarize the request for admin/debugging readability."""

        return f"SupportRequest({self.kind}, {self.status})"

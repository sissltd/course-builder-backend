from django.db import models
from django.utils.translation import gettext_lazy as _

from api.support.enums import SupportRequestStatus, SupportTicketMessageKind
from core.mixins import DateHistoryModelMixin, UUIDPrimaryKeyModelMixin


class SupportTicketMessage(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """One entry in a ticket's conversation thread (Figma admin ticket
    detail): a reply from the submitter or a support officer, or a
    "Ticket status changed to ..." event. The ticket's own description is
    the opening message and is not duplicated here."""

    ticket = models.ForeignKey(
        "support.SupportRequest",
        verbose_name=_("Ticket"),
        on_delete=models.CASCADE,
        related_name="messages",
    )
    kind = models.CharField(
        verbose_name=_("Kind"),
        max_length=13,
        choices=SupportTicketMessageKind.choices,
    )
    author = models.ForeignKey(
        "users.User",
        verbose_name=_("Author"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    body = models.TextField(verbose_name=_("Body"), blank=True)
    from_status = models.CharField(
        verbose_name=_("From Status"),
        max_length=12,
        choices=SupportRequestStatus.choices,
        blank=True,
    )
    to_status = models.CharField(
        verbose_name=_("To Status"),
        max_length=12,
        choices=SupportRequestStatus.choices,
        blank=True,
    )

    class Meta:
        verbose_name = _("Support Ticket Message")
        verbose_name_plural = _("Support Ticket Messages")
        ordering = ["created_datetime"]

    def __str__(self):
        """Summarize the entry for admin/debugging readability."""

        return f"SupportTicketMessage({self.kind}, ticket={self.ticket_id})"

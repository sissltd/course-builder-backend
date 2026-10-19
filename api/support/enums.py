from django.db import models
from django.utils.translation import gettext_lazy as _


class SupportRequestKind(models.TextChoices):
    """Which Support-page action created the request (Figma Help > Support)."""

    CONTACT = "CONTACT", _("Contact us")
    TICKET = "TICKET", _("Create ticket")
    APPEAL = "APPEAL", _("Request for an appeal")


class SupportRequestStatus(models.TextChoices):
    """Contact messages and appeals only move OPEN -> RESOLVED. Tickets use
    the full admin Tickets-page lifecycle (Figma: Opened, In-Progress,
    Resolved, Closed)."""

    OPEN = "OPEN", _("Open")
    IN_PROGRESS = "IN_PROGRESS", _("In-progress")
    RESOLVED = "RESOLVED", _("Resolved")
    CLOSED = "CLOSED", _("Closed")


class SupportTicketPriority(models.TextChoices):
    """Figma 'Submit a ticket' Priority dropdown / admin 'Priority Level'."""

    LOW = "LOW", _("Low")
    MEDIUM = "MEDIUM", _("Medium")
    HIGH = "HIGH", _("High")


class SupportTicketMessageKind(models.TextChoices):
    """One entry in a ticket's conversation thread."""

    REPLY = "REPLY", _("Reply")
    STATUS_CHANGE = "STATUS_CHANGE", _("Status change")

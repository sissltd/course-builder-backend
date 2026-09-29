from django.db import models
from django.utils.translation import gettext_lazy as _


class SupportRequestKind(models.TextChoices):
    """Which Support-page action created the request (Figma Help > Support)."""

    CONTACT = "CONTACT", _("Contact us")
    TICKET = "TICKET", _("Create ticket")
    APPEAL = "APPEAL", _("Request for an appeal")


class SupportRequestStatus(models.TextChoices):
    OPEN = "OPEN", _("Open")
    RESOLVED = "RESOLVED", _("Resolved")

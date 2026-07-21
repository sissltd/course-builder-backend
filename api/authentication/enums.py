from django.db import models


class TokenPurpose(models.TextChoices):
    """What an email verification token is authorizing.

    Extend with new purposes (e.g. sensitive account changes) as new flows
    are introduced, keeping values stable for auditability.
    """

    SIGNUP_VERIFICATION = "SIGNUP_VERIFICATION", "Signup Verification"
    PASSWORD_RESET = "PASSWORD_RESET", "Password Reset"
    STAFF_INVITATION = "STAFF_INVITATION", "Staff Invitation"

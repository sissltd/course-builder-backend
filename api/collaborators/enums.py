from django.db import models


class CollaboratorRole(models.TextChoices):
    """A course collaborator's access level. The course's own creator
    ("Author") is never stored as a row here - it's derived from
    Course.creator - so only these two values are ever persisted."""

    COLLABORATOR = "COLLABORATOR", "Collaborator"
    ADMIN = "ADMIN", "Admin"


class CollaboratorInviteStatus(models.TextChoices):
    """Lifecycle status of a collaborator invite (PRD Section 14).

    PENDING -> ACCEPTED | DECLINED (invitee decision, before expiry)
    PENDING -> REVOKED    (inviter cancels, any time before a decision)
    A PENDING invite past its expires_at is treated as expired: it can no
    longer be accepted/declined, but its stored status is left as PENDING
    so the inviter can see it lapsed rather than was revoked.
    """

    PENDING = "PENDING", "Pending"
    ACCEPTED = "ACCEPTED", "Accepted"
    DECLINED = "DECLINED", "Declined"
    REVOKED = "REVOKED", "Revoked"

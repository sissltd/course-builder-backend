from django.db import models


class CollaboratorRole(models.TextChoices):
    """A course collaborator's access level. The course's own creator
    ("Author") is never stored as a row here - it's derived from
    Course.creator - so only these two values are ever persisted."""

    COLLABORATOR = "COLLABORATOR", "Collaborator"
    ADMIN = "ADMIN", "Admin"

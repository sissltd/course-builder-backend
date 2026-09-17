from django.db import models
from django.db.models.functions import Lower
from django.utils.translation import gettext_lazy as _

from api.users.enums import UserRole
from core.mixins import (
    DateHistoryModelMixin,
    SoftDeleteModelMixin,
    UserHistoryModelMixin,
    UUIDPrimaryKeyModelMixin,
)


class Role(
    UUIDPrimaryKeyModelMixin,
    DateHistoryModelMixin,
    SoftDeleteModelMixin,
    UserHistoryModelMixin,
):
    """A named set of permissions users are assigned to.

    Built-in roles mirror `UserRole` one-to-one (`system_key` set). Custom
    roles have no `system_key`; each picks a `base_role`, which decides
    workflow behaviour that is not a permission - which review seats its
    members sit, MFA mandate, staff roster membership, login workspace.
    Whether a role is locked or deletable is derived in `policy.py`, never
    stored, so a database edit cannot unlock the Super Admin role.
    """

    name = models.CharField(
        verbose_name=_("Name"),
        max_length=60,
        help_text=_("Role name shown on the Roles & Permissions screen."),
    )
    description = models.TextField(
        verbose_name=_("Description"),
        blank=True,
        default="",
        help_text=_("Optional note on what the role is for."),
    )
    base_role = models.CharField(
        verbose_name=_("Base role"),
        max_length=20,
        choices=UserRole.choices,
        help_text=_(
            "The built-in role whose workflow behaviour members follow. Fixed "
            "once the role exists."
        ),
    )
    system_key = models.CharField(
        verbose_name=_("System key"),
        max_length=20,
        choices=UserRole.choices,
        null=True,
        blank=True,
        help_text=_("Set only on built-in roles: the UserRole this row represents."),
    )

    class Meta:
        verbose_name = _("Role")
        verbose_name_plural = _("Roles")
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["system_key"],
                condition=models.Q(system_key__isnull=False),
                name="role_unique_system_key",
            ),
            models.UniqueConstraint(
                Lower("name"),
                condition=models.Q(is_deleted=False),
                name="role_unique_live_name",
            ),
            models.CheckConstraint(
                condition=models.Q(system_key__isnull=True)
                | models.Q(system_key=models.F("base_role")),
                name="role_system_key_is_base_role",
            ),
        ]
        indexes = [
            models.Index(fields=["base_role", "is_deleted"], name="role_base_role_idx"),
        ]

    def __str__(self):
        return self.name


class RolePermission(UUIDPrimaryKeyModelMixin, DateHistoryModelMixin):
    """One permission granted to one role.

    `codename` is validated against `api.authorization.registry` when written
    through the service layer; a stored codename the registry no longer knows
    is ignored when permissions are resolved.
    """

    role = models.ForeignKey(
        Role,
        verbose_name=_("Role"),
        on_delete=models.CASCADE,
        related_name="grants",
        help_text=_("The role holding this permission."),
    )
    codename = models.CharField(
        verbose_name=_("Codename"),
        max_length=64,
        help_text=_("Permission codename, e.g. 'courses.approve'."),
    )

    class Meta:
        verbose_name = _("Role permission")
        verbose_name_plural = _("Role permissions")
        constraints = [
            models.UniqueConstraint(
                fields=["role", "codename"], name="role_permission_unique"
            ),
        ]
        indexes = [
            models.Index(
                fields=["codename", "role"], name="role_permission_codename_idx"
            ),
        ]

    def __str__(self):
        return f"{self.role_id}: {self.codename}"

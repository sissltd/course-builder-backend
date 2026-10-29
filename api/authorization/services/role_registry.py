"""Finding the built-in role that represents each UserRole."""

from django.db import transaction

from api.authorization.models import Role, RolePermission
from api.authorization.registry import SYSTEM_ROLE_DEFAULT_GRANTS
from api.users.enums import UserRole


def system_role_id(role: str):
    """Id of the built-in role for `role`, creating it with its defaults if absent.

    The seed migration creates these rows; the create path exists because
    test suites that flush tables (TransactionTestCase) remove them, and a
    user row must always be able to point at its system role. One query when
    the row exists.
    """

    existing = Role.objects.filter(system_key=role).values_list("id", flat=True).first()
    if existing is not None:
        return existing
    with transaction.atomic():
        created, was_created = Role.objects.get_or_create(
            system_key=role,
            defaults={"name": UserRole(role).label, "base_role": role},
        )
        if was_created:
            RolePermission.objects.bulk_create(
                [
                    RolePermission(role=created, codename=codename)
                    for codename in sorted(SYSTEM_ROLE_DEFAULT_GRANTS[role])
                ]
            )
    return created.id

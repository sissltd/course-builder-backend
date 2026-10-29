"""Rules about roles that are derived in code rather than stored.

Kept out of the database on purpose: a flag column could be flipped from
Django admin or a SQL console, and the Super Admin role being locked is not
something that should be one UPDATE away from changing.
"""

from api.users.enums import UserRole


def is_system(role) -> bool:
    return role.system_key is not None


def is_locked(role) -> bool:
    """The Super Admin role always holds every permission and cannot change."""

    return role.system_key == UserRole.SUPER_ADMIN


def is_deletable(role) -> bool:
    return not is_system(role)

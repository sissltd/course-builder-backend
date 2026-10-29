from datetime import timedelta

from django.utils import timezone
from rest_framework import exceptions

from api.courses.exceptions import ModuleLocked
from api.courses.models import Module
from api.users.models import User

DEFAULT_LOCK_TTL_MINUTES = 5


def acquire_collaboration_lock(*, module: Module, user: User) -> Module:
    """Persistently freeze collaborator writes on a module.

    Only the course creator may manage this state. The creator remains able to
    edit the module while collaborators are blocked by ``check_not_locked``.
    """

    if module.course.creator_id != user.id:
        raise exceptions.PermissionDenied(
            "Only the course creator can lock collaborator editing on a module."
        )
    if not module.collaboration_locked:
        module.collaboration_locked_by = user
        module.collaboration_locked_at = timezone.now()
        module.save(
            update_fields=[
                "collaboration_locked_by",
                "collaboration_locked_at",
                "updated_datetime",
            ]
        )
    return module


def release_collaboration_lock(*, module: Module, user: User) -> Module:
    """Remove the persistent collaborator freeze from a module."""

    if module.course.creator_id != user.id:
        raise exceptions.PermissionDenied(
            "Only the course creator can unlock collaborator editing on a module."
        )
    module.collaboration_locked_by = None
    module.collaboration_locked_at = None
    module.save(
        update_fields=[
            "collaboration_locked_by",
            "collaboration_locked_at",
            "updated_datetime",
        ]
    )
    return module


def acquire_lock(
    *, module: Module, user: User, ttl_minutes: int = DEFAULT_LOCK_TTL_MINUTES
) -> Module:
    """Acquire (or renew, if `user` already holds it) the edit lock on
    `module`. Raises ModuleLocked (423) if someone else currently holds an
    unexpired lock."""

    if module.is_locked and module.locked_by_id != user.id:
        raise ModuleLocked(
            f"This module is currently being edited by {module.locked_by}."
        )
    module.locked_by = user
    module.lock_expires_at = timezone.now() + timedelta(minutes=ttl_minutes)
    module.save(update_fields=["locked_by", "lock_expires_at", "updated_datetime"])
    return module


def release_lock(*, module: Module, user: User) -> Module:
    """Release the edit lock on `module`. No-op if not currently locked.
    Raises PermissionDenied if the lock is held by someone else."""

    if module.locked_by_id and module.locked_by_id != user.id:
        raise exceptions.PermissionDenied(
            "You do not hold the edit lock on this module."
        )
    module.locked_by = None
    module.lock_expires_at = None
    module.save(update_fields=["locked_by", "lock_expires_at", "updated_datetime"])
    return module


def heartbeat_lock(
    *, module: Module, user: User, ttl_minutes: int = DEFAULT_LOCK_TTL_MINUTES
) -> Module:
    """Extend `user`'s existing lock on `module`. Raises ModuleLocked if the
    lock isn't currently held (and unexpired) by `user`."""

    if module.locked_by_id != user.id or not module.is_locked:
        raise ModuleLocked("You do not hold an active edit lock on this module.")
    module.lock_expires_at = timezone.now() + timedelta(minutes=ttl_minutes)
    module.save(update_fields=["lock_expires_at", "updated_datetime"])
    return module


def check_not_locked(*, module: Module, user: User) -> None:
    """Raise ModuleLocked when a collaborator cannot write to ``module``."""

    if module.collaboration_locked and module.course.creator_id != user.id:
        raise ModuleLocked(
            "This module is locked by the course creator for collaborator editing."
        )

    if module.is_locked and module.locked_by_id != user.id:
        raise ModuleLocked(
            f"This module is currently being edited by {module.locked_by}."
        )

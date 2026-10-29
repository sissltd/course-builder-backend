"""Resolving and checking a user's permissions.

A user's permissions are their access role's stored grants, expanded by
`implies` and restricted to codenames the registry still knows. Superusers
and the Super Admin hold every permission without a query.

Cost is one query per request for everyone else, however many checks run:
the resolved set is memoised for the life of the request (see `apps.py`,
which opens and closes the memo on Django's request signals). Nothing is
cached across requests, so a grant change applies on the very next request
and a request never costs more or less depending on what ran before it.
Outside a request - Celery tasks, management commands - there is no memo and
each check reads the database, so a long-lived worker never holds stale
permissions.
"""

from collections.abc import Iterable
from contextvars import ContextVar

from django.db.models import Q, QuerySet
from rest_framework import exceptions

from api.authorization.models import RolePermission
from api.authorization.registry import ALL_CODENAMES, PERMISSIONS, expand
from api.users.enums import UserRole

DEFAULT_DENIED_MESSAGE = "You do not have permission to perform this action."

#: {(user id, access role id): permissions} for the current request, or None
#: outside one.
_request_memo: ContextVar[dict | None] = ContextVar("rbac_request_memo", default=None)


def open_request_memo(**kwargs) -> None:
    _request_memo.set({})


def close_request_memo(**kwargs) -> None:
    _request_memo.set(None)


def forget() -> None:
    """Drop memoised permissions, e.g. after changing grants mid-request."""

    if _request_memo.get() is not None:
        _request_memo.set({})


def get_permissions(user) -> frozenset:
    if user is None or not getattr(user, "is_authenticated", False):
        return frozenset()
    if user.is_superuser or user.role == UserRole.SUPER_ADMIN:
        return ALL_CODENAMES

    role_id = user.access_role_id
    if role_id is None:
        return frozenset()
    memo = _request_memo.get()
    key = (user.pk, role_id)
    if memo is not None and key in memo:
        return memo[key]

    permissions = expand(
        RolePermission.objects.filter(role_id=role_id).values_list(
            "codename", flat=True
        )
    )
    if memo is not None:
        memo[key] = permissions
    return permissions


def user_has_permission(user, codename: str) -> bool:
    return codename in get_permissions(user)


def user_has_any_permission(user, codenames: Iterable[str]) -> bool:
    held = get_permissions(user)
    return any(codename in held for codename in codenames)


def require_permission(
    user, codename: str, message: str = DEFAULT_DENIED_MESSAGE
) -> None:
    """Service-layer re-check: raise PermissionDenied unless `user` holds it."""

    if not user_has_permission(user, codename):
        raise exceptions.PermissionDenied(message)


def require_any_permission(
    user, codenames: Iterable[str], message: str = DEFAULT_DENIED_MESSAGE
) -> None:
    if not user_has_any_permission(user, codenames):
        raise exceptions.PermissionDenied(message)


def users_with_permission(codename: str) -> QuerySet:
    """Active users who hold `codename`, e.g. to notify whoever can act."""

    from api.users.models import User

    implying = [
        other.codename
        for other in PERMISSIONS.values()
        if codename in expand([other.codename])
    ]
    return User.objects.filter(
        Q(is_superuser=True)
        | Q(role=UserRole.SUPER_ADMIN)
        | Q(access_role__grants__codename__in=implying, access_role__is_deleted=False),
        is_active=True,
    ).distinct()

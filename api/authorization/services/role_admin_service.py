"""Managing roles: the Roles & Permissions screen's writes, and who holds which role.

Guard rails, applied to everyone except the Super Admin and superusers:

* Managing roles needs `roles.manage`.
* You may only add or remove permissions you hold yourself, so a manager can
  never hand out more than they have.
* You cannot edit the role you are assigned to, or touch the Super Admin role.
* You may only assign a role whose permissions you hold, to someone whose
  current permissions you also hold.

For everyone, including the Super Admin: permissions that act on other
people's accounts, money or platform settings can never be granted to the
public sign-up roles (Course Creator, Creator Reviewer), because that would
grant them to every self-registered user.

Editing a role's permissions takes effect on its members' next request:
permissions are read fresh on every request.
Moving a user to a different role also ends their sessions, because their
tokens carry the role they logged in with.
"""

from django.db import IntegrityError, transaction
from django.db.models import Case, Count, IntegerField, Q, QuerySet, Value, When
from django.utils import timezone
from rest_framework import exceptions
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)

from api.authentication.models import UserSession
from api.authentication.services import activity_service
from api.authorization import codenames, policy
from api.authorization.exceptions import RoleConflict, RoleHasMembers
from api.authorization.models import Role, RolePermission
from api.authorization.registry import ALL_CODENAMES, PERMISSIONS, PUBLIC_ROLES, expand
from api.authorization.services import permission_service
from api.notification.models import Notification
from api.users.enums import (
    INVITABLE_STAFF_ROLES,
    STAFF_ROLES,
    UserActivityActionEnums,
    UserActivityCategoryEnums,
    UserRole,
)
from api.users.models import User

#: Built-in roles list in the order UserRole declares them, custom roles after.
_SYSTEM_ORDER = Case(
    *[
        When(system_key=role, then=Value(index))
        for index, role in enumerate(UserRole.values)
    ],
    default=Value(len(UserRole.values)),
    output_field=IntegerField(),
)


# ── reads ──────────────────────────────────────────────────────────────────


def list_roles(*, actor: User) -> QuerySet[Role]:
    """Live roles with active member counts and grants. Two queries."""

    permission_service.require_permission(actor, codenames.ROLES_VIEW)
    return (
        Role.objects.filter(is_deleted=False)
        .annotate(member_count=Count("members", filter=Q(members__is_active=True)))
        .prefetch_related("grants")
        .order_by(_SYSTEM_ORDER, "name")
    )


def get_role(*, actor: User, role_id) -> Role:
    role = list_roles(actor=actor).filter(id=role_id).first()
    if role is None:
        raise exceptions.NotFound("Role not found.")
    return role


def list_members(*, actor: User, role: Role, search: str = "") -> QuerySet[User]:
    permission_service.require_permission(actor, codenames.ROLES_VIEW)
    permission_service.require_permission(actor, codenames.STAFF_VIEW)
    members = User.objects.filter(access_role=role).order_by("-created_datetime")
    if search:
        members = members.filter(
            Q(email__icontains=search)
            | Q(first_name__icontains=search)
            | Q(last_name__icontains=search)
        )
    return members


def granted_codenames(role: Role) -> list[str]:
    """The role's stored, still-registered permissions, sorted.

    Reads the prefetched `grants`, so it costs nothing on list_roles rows.
    """

    if policy.is_locked(role):
        return sorted(ALL_CODENAMES)
    return sorted(
        grant.codename for grant in role.grants.all() if grant.codename in PERMISSIONS
    )


def can_edit(*, actor: User, role: Role) -> bool:
    """Whether `actor` may change `role` at all (permission-level limits aside)."""

    if policy.is_locked(role):
        return False
    if _is_owner(actor):
        return True
    return (
        permission_service.user_has_permission(actor, codenames.ROLES_MANAGE)
        and actor.access_role_id != role.id
    )


def permission_catalogue(*, actor: User) -> list[dict]:
    """Every permission group and permission, marking what `actor` may grant."""

    from api.authorization.registry import PERMISSION_GROUPS

    permission_service.require_permission(actor, codenames.ROLES_VIEW)
    held = permission_service.get_permissions(actor)
    may_manage = _is_owner(actor) or codenames.ROLES_MANAGE in held
    return [
        {
            "key": group.key,
            "label": group.label,
            "is_design_group": group.is_design_group,
            "permissions": [
                {
                    "codename": permission.codename,
                    "label": permission.label,
                    "description": permission.description,
                    "implies": list(permission.implies),
                    "grantable_to_public_roles": permission.grantable_to_public_roles,
                    "grantable_by_you": may_manage
                    and (_is_owner(actor) or permission.codename in held),
                }
                for permission in PERMISSIONS.values()
                if permission.group == group.key
            ],
        }
        for group in PERMISSION_GROUPS
    ]


# ── writes ─────────────────────────────────────────────────────────────────


def create_role(
    *,
    actor: User,
    name: str,
    description: str,
    base_role: str,
    permissions: list[str],
    request=None,
) -> Role:
    permission_service.require_permission(actor, codenames.ROLES_MANAGE)
    if base_role not in INVITABLE_STAFF_ROLES:
        raise exceptions.ValidationError(
            {"base_role": "A custom role must be based on a staff role."}
        )
    requested = _validated_codenames(permissions)
    _assert_can_change(actor=actor, changed=requested)
    _assert_name_free(name=name)

    with transaction.atomic():
        try:
            with transaction.atomic():
                role = Role.objects.create(
                    name=name,
                    description=description,
                    base_role=base_role,
                    created_by=actor,
                    updated_by=actor,
                )
        except IntegrityError as exc:
            raise RoleConflict() from exc
        RolePermission.objects.bulk_create(
            [RolePermission(role=role, codename=code) for code in sorted(requested)]
        )
        activity_service.log_activity(
            user=actor,
            category=UserActivityCategoryEnums.CONFIGURATION,
            action=UserActivityActionEnums.ROLE_CREATED,
            summary=f"Created the '{role.name}' role.",
            details={"role_id": str(role.id), "permissions": sorted(requested)},
            target=role,
            request=request,
        )
    return get_role(actor=actor, role_id=role.id)


def update_role(
    *,
    actor: User,
    role: Role,
    name: str | None = None,
    description: str | None = None,
    permissions: list[str] | None = None,
    request=None,
) -> Role:
    permission_service.require_permission(actor, codenames.ROLES_MANAGE)
    _assert_role_editable(actor=actor, role=role)
    if policy.is_system(role) and (name is not None or description is not None):
        raise exceptions.ValidationError(
            "A built-in role's name and description cannot be changed; only its permissions."
        )

    current = set(granted_codenames(role))
    added = removed = set()
    if permissions is not None:
        requested = _validated_codenames(permissions)
        added, removed = requested - current, current - requested
        _assert_can_change(actor=actor, changed=added | removed)
        if role.system_key in PUBLIC_ROLES:
            _assert_public_grantable(added)

    changed_fields = []
    if name is not None and name != role.name:
        _assert_name_free(name=name, exclude_id=role.id)
        role.name = name
        changed_fields.append("name")
    if description is not None and description != role.description:
        role.description = description
        changed_fields.append("description")

    with transaction.atomic():
        if changed_fields:
            role.updated_by = actor
            try:
                with transaction.atomic():
                    role.save(
                        update_fields=[
                            *changed_fields,
                            "updated_by",
                            "updated_datetime",
                        ]
                    )
            except IntegrityError as exc:
                raise RoleConflict() from exc
        if added or removed:
            RolePermission.objects.filter(role=role, codename__in=removed).delete()
            RolePermission.objects.bulk_create(
                [RolePermission(role=role, codename=code) for code in sorted(added)]
            )
            Role.objects.filter(id=role.id).update(
                updated_by=actor, updated_datetime=timezone.now()
            )
            permission_service.forget()
        if changed_fields or added or removed:
            activity_service.log_activity(
                user=actor,
                category=UserActivityCategoryEnums.CONFIGURATION,
                action=UserActivityActionEnums.ROLE_UPDATED,
                summary=f"Updated the '{role.name}' role.",
                details={
                    "role_id": str(role.id),
                    "changed_fields": changed_fields,
                    "permissions_added": sorted(added),
                    "permissions_removed": sorted(removed),
                },
                target=role,
                request=request,
            )
    return get_role(actor=actor, role_id=role.id)


def delete_role(
    *, actor: User, role: Role, reassign_to_role_id=None, request=None
) -> dict:
    """Soft-delete a custom role, moving its members to another role first.

    A role with members needs `reassign_to_role_id` (409 otherwise). The
    target must be live and share the base role, so members keep their
    workflow. Moved members are signed out.
    """

    permission_service.require_permission(actor, codenames.ROLES_MANAGE)
    if not policy.is_deletable(role):
        raise exceptions.PermissionDenied("Built-in roles cannot be deleted.")
    _assert_role_editable(actor=actor, role=role)

    member_ids = list(
        User.objects.filter(access_role=role).values_list("id", flat=True)
    )
    target = None
    if member_ids:
        if reassign_to_role_id is None:
            raise RoleHasMembers(
                f"'{role.name}' still has {len(member_ids)} member(s). Choose a role "
                "to move them to."
            )
        target = Role.objects.filter(id=reassign_to_role_id, is_deleted=False).first()
        if target is None or target.id == role.id:
            raise exceptions.ValidationError(
                {"reassign_to_role_id": "Choose another live role to move members to."}
            )
        if target.base_role != role.base_role:
            raise exceptions.ValidationError(
                {
                    "reassign_to_role_id": "Members can only move to a role with the same base role."
                }
            )
        _assert_can_assign(actor=actor, role=target)

    with transaction.atomic():
        members = list(User.objects.filter(id__in=member_ids))
        if target is not None:
            User.objects.filter(id__in=member_ids).update(access_role=target)
            revoke_sessions_bulk(user_ids=member_ids)
            activity_service.bulk_log_activity(
                entries=[
                    {
                        "user": member,
                        "actor_user": actor,
                        "category": UserActivityCategoryEnums.CONFIGURATION,
                        "action": UserActivityActionEnums.STAFF_ROLE_CHANGED,
                        "summary": f"Moved to the '{target.name}' role.",
                        "details": {
                            "from_role_id": str(role.id),
                            "to_role_id": str(target.id),
                        },
                        "target": target,
                    }
                    for member in members
                ]
            )
        role.is_deleted = True
        role.deleted_datetime = timezone.now()
        role.updated_by = actor
        role.save(
            update_fields=[
                "is_deleted",
                "deleted_datetime",
                "updated_by",
                "updated_datetime",
            ]
        )
        activity_service.log_activity(
            user=actor,
            category=UserActivityCategoryEnums.CONFIGURATION,
            action=UserActivityActionEnums.ROLE_DELETED,
            summary=f"Deleted the '{role.name}' role.",
            details={
                "role_id": str(role.id),
                "members_moved": len(member_ids),
                "moved_to_role_id": str(target.id) if target else None,
            },
            target=role,
            request=request,
        )
    return {
        "role_id": role.id,
        "members_moved": len(member_ids),
        "moved_to_role": target,
    }


def change_user_role(*, actor: User, user: User, role: Role, request=None) -> User:
    """Move a staff member to another staff role, built-in or custom."""

    permission_service.require_permission(actor, codenames.STAFF_FULL_ACCESS)
    if user.id == actor.id:
        raise exceptions.ValidationError("You cannot change your own role.")
    if user.role == UserRole.SUPER_ADMIN or user.is_superuser:
        raise exceptions.PermissionDenied("The Super Admin's role cannot be changed.")
    if user.role not in STAFF_ROLES:
        raise exceptions.NotFound("Staff member not found.")
    if role.is_deleted or role.base_role not in INVITABLE_STAFF_ROLES:
        raise exceptions.ValidationError({"role_id": "Choose a live staff role."})
    if user.access_role_id == role.id:
        raise exceptions.ValidationError(
            {"role_id": "This staff member already holds that role."}
        )
    _assert_can_assign(actor=actor, role=role)
    _assert_outranks(actor=actor, user=user)

    from api.reviews.services import review_service

    previous_role = user.access_role
    with transaction.atomic():
        user.role = role.base_role
        user.access_role = role
        user.save(update_fields=["role", "access_role", "updated_datetime"])
        released = review_service.release_seats_after_role_change(user=user)
        revoke_sessions_bulk(user_ids=[user.id])
        details = {
            "user_id": str(user.id),
            "from_role_id": str(previous_role.id),
            "to_role_id": str(role.id),
            "seats_released": released,
        }
        for log_user, summary in (
            (user, f"Your role changed to '{role.name}'."),
            (actor, f"Changed {user.email}'s role to '{role.name}'."),
        ):
            activity_service.log_activity(
                user=log_user,
                actor_user=actor,
                category=UserActivityCategoryEnums.CONFIGURATION,
                action=UserActivityActionEnums.STAFF_ROLE_CHANGED,
                summary=summary,
                details=details,
                target=role,
                request=request,
            )
        Notification.emit_in_app_notification(
            receivers=[user],
            title="Your role changed",
            content=f"Your role is now '{role.name}'. Please sign in again.",
            metadata={"role_id": role.id},
            critical=True,
        )
    return user


def assert_can_invite_to(*, actor: User, role: Role) -> None:
    """Staff invites may only use a role the inviter could assign."""

    if role.is_deleted or role.base_role not in INVITABLE_STAFF_ROLES:
        raise exceptions.ValidationError({"role_id": "Choose a live staff role."})
    _assert_can_assign(actor=actor, role=role)


def revoke_sessions_bulk(*, user_ids: list) -> None:
    """End every session for `user_ids` in a fixed number of queries."""

    if not user_ids:
        return
    BlacklistedToken.objects.bulk_create(
        [
            BlacklistedToken(token_id=token_id)
            for token_id in OutstandingToken.objects.filter(
                user_id__in=user_ids
            ).values_list("id", flat=True)
        ],
        ignore_conflicts=True,
    )
    UserSession.objects.filter(user_id__in=user_ids, revoked_at__isnull=True).update(
        revoked_at=timezone.now()
    )


# ── guards ─────────────────────────────────────────────────────────────────


def _is_owner(actor: User) -> bool:
    return bool(actor.is_superuser or actor.role == UserRole.SUPER_ADMIN)


def _validated_codenames(permissions) -> set:
    requested = set(permissions)
    unknown = sorted(requested - ALL_CODENAMES)
    if unknown:
        raise exceptions.ValidationError(
            {"permissions": f"Unknown permissions: {', '.join(unknown)}."}
        )
    return requested


def _assert_can_change(*, actor: User, changed: set) -> None:
    if _is_owner(actor) or not changed:
        return
    missing = sorted(expand(changed) - permission_service.get_permissions(actor))
    if missing:
        raise exceptions.PermissionDenied(
            "You can only grant or remove permissions you hold yourself. "
            f"Missing: {', '.join(missing)}."
        )


def _assert_public_grantable(added: set) -> None:
    refused = sorted(
        code for code in added if not PERMISSIONS[code].grantable_to_public_roles
    )
    if refused:
        raise exceptions.ValidationError(
            {
                "permissions": (
                    "These permissions cannot be granted to a public sign-up role, "
                    f"since every self-registered user would hold them: {', '.join(refused)}."
                )
            }
        )


def _assert_role_editable(*, actor: User, role: Role) -> None:
    if policy.is_locked(role):
        raise exceptions.PermissionDenied("The Super Admin role cannot be changed.")
    if not _is_owner(actor) and actor.access_role_id == role.id:
        raise exceptions.PermissionDenied("You cannot change the role you hold.")


def _assert_can_assign(*, actor: User, role: Role) -> None:
    if _is_owner(actor):
        return
    if policy.is_locked(role):
        raise exceptions.PermissionDenied(
            "Only the Super Admin role's holder can assign it."
        )
    missing = sorted(
        expand(granted_codenames(role)) - permission_service.get_permissions(actor)
    )
    if missing:
        raise exceptions.PermissionDenied(
            "You can only assign a role whose permissions you hold yourself."
        )


def _assert_outranks(*, actor: User, user: User) -> None:
    if _is_owner(actor):
        return
    if not permission_service.get_permissions(
        user
    ) <= permission_service.get_permissions(actor):
        raise exceptions.PermissionDenied(
            "You cannot manage someone who holds permissions you don't."
        )


def _assert_name_free(*, name: str, exclude_id=None) -> None:
    live = Role.objects.filter(is_deleted=False, name__iexact=name)
    if exclude_id is not None:
        live = live.exclude(id=exclude_id)
    if live.exists():
        raise RoleConflict(f"A role named '{name}' already exists.")

from django.db import transaction
from django.db.models import QuerySet
from rest_framework import exceptions

from api.authentication.services import activity_service
from api.authentication.services.authentication_service import AuthenticationService
from api.notification.models import Notification
from api.users.enums import (
    AccountStatus,
    UserActivityActionEnums,
    UserRole,
)
from api.users.models import User
from api.users.permissions import IsAdminOrSuperAdminRole, require_role

#: Roles this service refuses to act on. Suspending a peer Admin - or the
#: platform owner - is an employment decision, not a moderation one, and it
#: belongs to the Super Admin-only staff endpoints
#: (authentication.staff_service.revoke_staff). Keeping the two apart means an
#: Admin cannot disable the tier that supervises them, and the Super Admin seat
#: stays unrevocable through every route, not just the staff one.
PRIVILEGED_ROLES = (UserRole.ADMIN, UserRole.SUPER_ADMIN)

#: Statuses a suspended/deactivated account can be restored from. A
#: PENDING_VERIFICATION account is not "restorable" - it was never active, and
#: reinstating it would skip email verification.
REINSTATABLE_STATUSES = (AccountStatus.SUSPENDED, AccountStatus.DEACTIVATED)


def list_users(*, actor: User) -> QuerySet[User]:
    """Return every user account for the admin roster, newest first.

    Unlike the Super Admin-only staff roster (which lists only STAFF_ROLES),
    this covers the whole user base - the public Course Creators an Admin
    actually needs to moderate are invisible on the Teams page.
    """

    require_role(actor, IsAdminOrSuperAdminRole.allowed_roles)
    return User.objects.all().order_by("-created_datetime")


def get_user(*, actor: User, user_id) -> User:
    """Return one user account by id. Raises NotFound if it doesn't exist."""

    require_role(actor, IsAdminOrSuperAdminRole.allowed_roles)
    user = User.objects.filter(pk=user_id).first()
    if user is None:
        raise exceptions.NotFound("User not found.")
    return user


def suspend_user(*, actor: User, user: User, reason: str, request=None) -> User:
    """Suspend `user` for policy reasons, ending their sessions immediately.

    This is the only code path that ever assigns AccountStatus.SUSPENDED -
    login and token refresh have always rejected that status, but until now
    nothing could set it, so the policy was unenforceable.

    Sets is_active=False alongside the status (the model documents is_active as
    the authentication gate, kept in sync with status) and blacklists every
    outstanding refresh token, so an already-signed-in abuser is cut off at
    their next refresh rather than lingering for the life of their session.
    Raises ValidationError if the account is already suspended.
    """

    require_role(actor, IsAdminOrSuperAdminRole.allowed_roles)
    _assert_moderatable(actor=actor, user=user, verb="suspend")

    if user.status == AccountStatus.SUSPENDED:
        raise exceptions.ValidationError("This account is already suspended.")

    with transaction.atomic():
        user.is_active = False
        user.status = AccountStatus.SUSPENDED
        user.save(update_fields=["is_active", "status", "updated_datetime"])
        _revoke_sessions(user=user)

        Notification.emit_in_app_notification(
            receivers=[user],
            title="Your account has been suspended",
            content=f"Your account has been suspended: {reason}",
            metadata={"reason": reason},
        )
        activity_service.log_auth_activity(
            user=actor,
            action=UserActivityActionEnums.ACCOUNT_SUSPENDED,
            summary=f"Suspended {user.email}.",
            request=request,
            details={"target_email": user.email, "reason": reason},
        )

    return user


def deactivate_user(*, actor: User, user: User, reason: str, request=None) -> User:
    """Deactivate `user`, the terminal counterpart to suspension.

    Suspension is corrective and expected to be lifted; deactivation is the
    end of the account's life on the platform. Both are reversible via
    reinstate_user - the row is never deleted, so activity logs, created_by
    references, and authored courses keep pointing at a real user (same
    reasoning as staff_service.revoke_staff).
    """

    require_role(actor, IsAdminOrSuperAdminRole.allowed_roles)
    _assert_moderatable(actor=actor, user=user, verb="deactivate")

    if user.status == AccountStatus.DEACTIVATED:
        raise exceptions.ValidationError("This account is already deactivated.")

    with transaction.atomic():
        user.is_active = False
        user.status = AccountStatus.DEACTIVATED
        user.save(update_fields=["is_active", "status", "updated_datetime"])
        _revoke_sessions(user=user)

        activity_service.log_auth_activity(
            user=actor,
            action=UserActivityActionEnums.ACCOUNT_DEACTIVATED,
            summary=f"Deactivated {user.email}.",
            request=request,
            details={"target_email": user.email, "reason": reason},
        )

    return user


def reinstate_user(*, actor: User, user: User, request=None) -> User:
    """Restore a suspended or deactivated account to ACTIVE.

    Refuses accounts with an unusable password: those were invited and never
    accepted, so flipping them active would leave someone active with no way
    to sign in - they need a fresh invitation instead (mirrors
    staff_service.reactivate_staff).
    """

    require_role(actor, IsAdminOrSuperAdminRole.allowed_roles)
    _assert_moderatable(actor=actor, user=user, verb="reinstate")

    if user.status not in REINSTATABLE_STATUSES:
        raise exceptions.ValidationError(
            f"An account with status '{user.status}' cannot be reinstated."
        )

    if not user.has_usable_password():
        raise exceptions.ValidationError(
            "This account has no usable password. Send a fresh invitation instead."
        )

    with transaction.atomic():
        user.is_active = True
        user.status = AccountStatus.ACTIVE
        user.save(update_fields=["is_active", "status", "updated_datetime"])

        Notification.emit_in_app_notification(
            receivers=[user],
            title="Your account has been reinstated",
            content="Your account is active again and you can sign in as usual.",
            metadata={},
        )
        activity_service.log_auth_activity(
            user=actor,
            action=UserActivityActionEnums.ACCOUNT_REINSTATED,
            summary=f"Reinstated {user.email}.",
            request=request,
            details={"target_email": user.email},
        )

    return user


def _assert_moderatable(*, actor: User, user: User, verb: str) -> None:
    """Guard the ways account moderation could be turned against the platform."""

    if user.id == actor.id:
        raise exceptions.ValidationError(f"You cannot {verb} your own account.")

    if user.role in PRIVILEGED_ROLES:
        raise exceptions.ValidationError(
            f"You cannot {verb} an admin or super admin account here. "
            "Use the staff endpoints instead."
        )


def _revoke_sessions(user: User) -> None:
    """Blacklist every outstanding refresh token for `user`.

    Only refresh tokens are revocable - an access token already in a suspended
    user's hands keeps working until it expires (the limitation
    AuthenticationService.logout_all_sessions documents). Sessions therefore
    end at the next refresh, not instantly.
    """

    AuthenticationService().logout_all_sessions(user=user)

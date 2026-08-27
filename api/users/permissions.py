from django.conf import settings
from rest_framework import exceptions
from rest_framework.permissions import BasePermission

from api.users.enums import UserRole


class HasRole(BasePermission):
    """Base permission that grants access when the requesting user's role is allowed.

    Superusers are always granted access regardless of `allowed_roles`, matching
    Django's convention that is_superuser bypasses ordinary authorization checks.
    Subclass and set `allowed_roles` to a tuple of `UserRole` values.
    """

    allowed_roles: tuple = ()

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        return bool(user.is_superuser or user.role in self.allowed_roles)


def require_role(
    user, allowed_roles, message="You do not have permission to perform this action."
):
    """Raise PermissionDenied unless `user` is a superuser or holds one of
    `allowed_roles`. Mirrors HasRole.has_permission's bypass/check logic, for
    use inside service functions as defense-in-depth alongside view-level
    permission classes - so a service called from a code path that doesn't
    apply the same permission class still enforces the same role rule."""

    if not (user.is_superuser or user.role in allowed_roles):
        raise exceptions.PermissionDenied(message)


class IsCourseCreatorRole(HasRole):
    """Grants access to users who author courses.

    Covers both the public Course Creator and the invited Writer - they do the
    same job, so they share one permission rather than duplicating every
    authoring endpoint.
    """

    allowed_roles = (UserRole.COURSE_CREATOR, UserRole.STAFF_WRITER)


class IsCreatorReviewerRole(HasRole):
    """Grants access to users who review submitted courses.

    Covers the Creator Reviewer and the invited Verifier.
    """

    allowed_roles = (UserRole.CREATOR_REVIEWER, UserRole.STAFF_VERIFIER)


class IsAiReviewerRole(HasRole):
    """Grants access to users who review AI-produced courses (SCCS Track B).

    No view uses this yet - the AI Auto-Production review pipeline isn't
    built in this backend - but the role is already invitable
    (INVITABLE_STAFF_ROLES), so it needs to be enforceable the moment such a
    view exists.
    """

    allowed_roles = (UserRole.AI_REVIEWER,)


class IsQaReviewerRole(HasRole):
    """Grants access to users who QA production media output (SCCS Track B).

    Same rationale as IsAiReviewerRole: no consuming view yet, added for
    enforceability ahead of the QA review pipeline.
    """

    allowed_roles = (UserRole.QA_REVIEWER,)


class IsAdminRole(HasRole):
    """Grants access to the platform's administrative tier.

    Covers Admins, invited Approvers (whose job is exactly the admin approval
    step), and Super Admins - who outrank Admins, so anything an Admin can do a
    Super Admin can do too.
    """

    allowed_roles = (
        UserRole.ADMIN,
        UserRole.STAFF_APPROVER,
        UserRole.SUPER_ADMIN,
    )


class CanManageCategories(HasRole):
    """Grants create/update/delete on course categories.

    Deliberately NOT `IsCourseCreatorRole`: that class also covers the public
    COURSE_CREATOR role, and category management is a staff responsibility -
    public creators only browse categories to pick one for a course.

    Note this excludes Approvers, who keep read access like everyone else.
    Categories carry `creator_price`, so a Writer/Admin editing one can affect
    what a creator's submissions pay; that is an accepted trust decision for
    salaried staff rather than an oversight.
    """

    allowed_roles = (UserRole.STAFF_WRITER, UserRole.ADMIN, UserRole.SUPER_ADMIN)


class IsAdminOrSuperAdminRole(HasRole):
    """Grants access to Admins and Super Admins only - deliberately excludes
    STAFF_APPROVER, unlike IsAdminRole.

    Used for KYC review: identity-verification decisions (and the new-request
    alert) are kept to the platform's own administrative tier rather than
    extended to invited Approver staff, who only handle course approvals.
    """

    allowed_roles = (UserRole.ADMIN, UserRole.SUPER_ADMIN)


class IsSuperAdminRole(HasRole):
    """Grants access to users with the Super Admin role only.

    Use this for platform-ownership operations - inviting and revoking staff,
    and any future settings an ordinary Admin must not be able to change.
    """

    allowed_roles = (UserRole.SUPER_ADMIN,)


class IsMFAVerifiedForSession(BasePermission):
    """Baseline session-level MFA gate for sensitive administrative actions
    (PlatformSettings updates, category pricing).

    Checks the "mfa_verified" claim LoginSerializer/mfa_views embed on the
    access token - True for a role MFA was never mandatory for, or a
    mandated role that actually completed an MFA challenge; False for a
    mandated role logged in during (or past) its enrollment grace period
    with no device yet. Compose alongside a role permission via DRF's
    ANDed permission_classes list - this class does not check role itself.

    request.auth is the validated AccessToken for JWT-authenticated
    requests (supports dict-style .get); anything else (no token, a
    different auth scheme) is treated as not verified.
    """

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False

        if not settings.MFA_ENFORCED:
            # Non-production deployment - MFA is not mandated, so there is
            # nothing to verify regardless of role or token claim.
            return True

        from api.authentication.services import mfa_service

        if user.role not in mfa_service.MFA_MANDATED_ROLES:
            # This role was never required to have MFA - nothing to verify.
            return True

        auth = getattr(request, "auth", None)
        if auth is None:
            return False
        return bool(auth.get("mfa_verified"))

"""Session-level permission classes that are not about what a user may do.

What a user may do is decided by stored role permissions - see
`api.authorization.permissions.Perm` and `permission_service`. The per-role
gate classes that used to live here were replaced by those;
`api/authorization/tests/test_no_role_gates.py` keeps them from coming back.
"""

from django.conf import settings
from rest_framework.permissions import BasePermission


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
        # A token minted under an earlier role must not carry its MFA state
        # into a role that mandates MFA (see api.authorization role changes).
        return (
            bool(auth.get("mfa_verified")) and auth.get("role", user.role) == user.role
        )

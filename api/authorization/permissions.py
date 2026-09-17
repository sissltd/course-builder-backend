"""DRF permission classes backed by stored role permissions."""

from django.conf import settings
from rest_framework.permissions import BasePermission

from api.authorization.services import permission_service


class HasPermission(BasePermission):
    """Admit authenticated users holding any of `codenames`.

    Declare gates with `Perm(...)` rather than subclassing by hand; the result
    composes with `&` / `|` like any DRF permission class.
    """

    codenames: tuple = ()

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        return permission_service.user_has_any_permission(user, self.codenames)


def Perm(*codenames: str) -> type[HasPermission]:  # noqa: N802 - reads as a class at call sites
    """A HasPermission class admitting holders of any of `codenames`."""

    if not codenames:
        raise ValueError("Perm() needs at least one codename.")
    name = "Perm_" + "_or_".join(code.replace(".", "_") for code in codenames)
    return type(name, (HasPermission,), {"codenames": tuple(codenames)})


class IsStrongMFASession(BasePermission):
    """Require an MFA-verified session for this action, whatever the caller's role.

    For money and identity actions a custom role could otherwise reach with a
    password alone (MFA is only mandated for some base roles). Inert where
    MFA is not enforced.

    Reads `mfa_challenged`, not `mfa_verified`: login mints `mfa_verified`
    true without a challenge for roles MFA is not mandatory for, whereas
    `mfa_challenged` is only set when the session actually passed an MFA
    challenge. The token's role claim must also match the user's current
    role, so a session opened under an earlier role does not carry its MFA
    state across a role change.
    """

    message = "This action needs a session verified with multi-factor authentication."

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if not settings.MFA_ENFORCED:
            return True
        auth = getattr(request, "auth", None)
        if auth is None:
            return False
        return bool(auth.get("mfa_challenged")) and auth.get("role") == user.role

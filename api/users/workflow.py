"""Checks on a user's workflow role (`User.role`), as opposed to permissions.

Some behaviour belongs to *what kind of user* someone is rather than to a
grantable permission: a reviewer's workspace dashboard, which review seats
they sit, whether MFA is mandatory. Those stay tied to `User.role` - a custom
role's base role - and are checked here, so a grep for access checks
(`api.authorization.services.permission_service`) never has to guess which
kind of check it is looking at.
"""

from rest_framework import exceptions

from api.users.enums import UserRole

#: Roles that author courses and earn from them: they earn achievement badges
#: and hold a wallet. Workflow membership, not a permission.
EARNING_ROLES = (UserRole.COURSE_CREATOR, UserRole.STAFF_WRITER)

#: Roles whose home is the reviewer workspace (dashboard, activity overview).
REVIEWER_WORKSPACE_ROLES = (UserRole.CREATOR_REVIEWER, UserRole.STAFF_VERIFIER)


def require_base_role(
    user, roles, message="This workspace is not available for your role."
) -> None:
    """Raise PermissionDenied unless `user`'s workflow role is in `roles`.

    Superusers pass, matching how every other gate treats them.
    """

    if not (user.is_superuser or user.role in roles):
        raise exceptions.PermissionDenied(message)

"""Admin actions on someone else's account that don't change its status.

Staff and Teams are one set of accounts. Each action is still exposed twice -
under the Staff route and chips, and under the Teams route and chips - so a
role can be granted either, but both routes reach the same accounts. The one
exception is privileged accounts (Admin, Super Admin): those are managed only
under the Staff permissions, so a Teams permission can never be used to act
on the tier that supervises it.
"""

from django.db import transaction
from rest_framework import exceptions

from api.authentication.enums import TokenPurpose
from api.authentication.services import activity_service, token_service
from api.authentication.services.authentication_service import AuthenticationService
from api.authorization import codenames
from api.authorization.services import permission_service
from api.users.enums import (
    PRIVILEGED_ROLES,
    AccountStatus,
    UserActivityActionEnums,
    UserActivityCategoryEnums,
    UserRole,
)
from api.users.models import User

STAFF = "staff"
TEAMS = "teams"

RESET_PASSWORD_PERMISSION = {
    STAFF: codenames.STAFF_RESET_PASSWORD,
    TEAMS: codenames.TEAMS_RESET_PASSWORD,
}


def resolve_target(*, audience: str, user_id) -> User:
    """The account `user_id`, else 404.

    A privileged account is 404 under the Teams audience, as it is for an id
    that does not exist, so the Teams routes cannot confirm which ids belong
    to Admins.
    """

    user = User.objects.filter(pk=user_id).first()
    if user is None or (audience == TEAMS and user.role in PRIVILEGED_ROLES):
        raise exceptions.NotFound("Account not found.")
    return user


def send_password_reset(
    *, actor: User, user: User, audience: str, request=None
) -> None:
    """Email `user` a password reset link, as if they had asked for one.

    Nothing about the account changes until they use the link; completing
    the reset signs them out everywhere, as a self-service reset does.
    """

    permission_service.require_permission(actor, RESET_PASSWORD_PERMISSION[audience])
    if user.id == actor.id:
        raise exceptions.ValidationError(
            "Use Forgot password to reset your own password."
        )
    if user.is_superuser or user.role == UserRole.SUPER_ADMIN:
        raise exceptions.PermissionDenied(
            "The Super Admin's password cannot be reset here."
        )
    if not user.has_usable_password():
        raise exceptions.ValidationError(
            "This account has not been set up yet; resend its invitation instead."
        )
    if user.status != AccountStatus.ACTIVE:
        raise exceptions.ValidationError(
            "Only an active account can be sent a reset link."
        )
    if not token_service.can_resend(user=user, purpose=TokenPurpose.PASSWORD_RESET):
        raise exceptions.ValidationError(
            "A reset link was sent moments ago. Please wait before sending another."
        )

    with transaction.atomic():
        _token, raw_token = token_service.issue_token(
            user=user, purpose=TokenPurpose.PASSWORD_RESET
        )
        activity_service.log_activity(
            user=user,
            actor_user=actor,
            category=UserActivityCategoryEnums.AUTH,
            action=UserActivityActionEnums.PASSWORD_RESET_SENT_BY_ADMIN,
            summary="A password reset link was sent by an administrator.",
            request=request,
        )
        transaction.on_commit(
            lambda: AuthenticationService.send_password_reset_email(
                user=user, raw_token=raw_token
            )
        )

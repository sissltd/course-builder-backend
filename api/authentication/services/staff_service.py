import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef
from rest_framework import exceptions

from api.authentication.enums import TokenPurpose
from api.authentication.models import EmailVerificationToken
from api.authentication.services import activity_service, token_service
from api.authentication.utils.links import build_verification_link
from api.users.enums import (
    AccountStatus,
    STAFF_ROLES,
    UserActivityActionEnums,
    UserRole,
)
from shared.tasks import send_templated_email_task

User = get_user_model()
logger = logging.getLogger(__name__)

STAFF_INVITATION_SUBJECT = "You've been invited to join the team"


def _open_invitation_subquery(user_ref=OuterRef("pk")):
    """Unused, unexpired-or-not invitation tokens belonging to a user.

    Expiry is deliberately not filtered: an expired-but-unburned invitation is
    still "outstanding" from the Teams page's point of view, and the fix for it
    is a resend either way.
    """

    return EmailVerificationToken.objects.filter(
        user=user_ref,
        purpose=TokenPurpose.STAFF_INVITATION,
        is_used=False,
    )


class StaffService:
    """Provisioning and lifecycle for the platform's staff accounts.

    Staff are never self-service. Public signup hardcodes COURSE_CREATOR, and
    every method here sets `role` from a server-side allowlist, so a client
    cannot escalate by posting a role field anywhere in the API.

    Two entry paths exist, and they are deliberately asymmetric:

    * `bootstrap_superadmin` runs exactly once in enabled environments - there
      is nobody to authenticate as before the first account exists.
    * `invite_staff` / `accept_staff_invitation` is the ongoing flow, gated the
      normal way: only an authenticated Super Admin can invite, and the invitee
      proves control of their inbox before the account activates.
    """

    def bootstrap_superadmin(
        self,
        *,
        email: str,
        password: str,
        first_name: str,
        last_name: str,
        request=None,
    ) -> User:
        """Claim the platform's single Super Admin seat.

        Gated by two independent conditions, both of which must hold:

        1. The deployment environment enables bootstrap.
        2. No Super Admin exists yet.

        The second check is backed by a partial unique constraint on the users
        table, so two simultaneous requests cannot both win the race: one
        commits, the other raises IntegrityError and is reported as a 400
        exactly like the sequential case.
        """

        if not settings.SUPERADMIN_BOOTSTRAP_ENABLED:
            raise exceptions.PermissionDenied(
                "Super admin bootstrap is disabled on this deployment."
            )

        if User.objects.filter(role=UserRole.SUPER_ADMIN).exists():
            raise exceptions.ValidationError(
                "A super admin already exists for this platform."
            )

        if User.objects.filter(email__iexact=email).exists():
            raise exceptions.ValidationError(
                {"email": "A user with this email already exists."}
            )

        try:
            with transaction.atomic():
                user = User.objects.create_user(
                    email=email,
                    password=password,
                    first_name=first_name,
                    last_name=last_name,
                    role=UserRole.SUPER_ADMIN,
                    # Active immediately: there is no one to verify this account
                    # for it, and the env secret already proved server access.
                    is_active=True,
                    status=AccountStatus.ACTIVE,
                    # Django-level flags kept in step with the app role so the
                    # Super Admin can also reach /admin/.
                    is_staff=True,
                    is_superuser=True,
                )
        except IntegrityError as exc:
            # Lost the race against a concurrent bootstrap request.
            raise exceptions.ValidationError(
                "A super admin already exists for this platform."
            ) from exc

        activity_service.log_auth_activity(
            user=user,
            action=UserActivityActionEnums.SUPERADMIN_BOOTSTRAPPED,
            summary="Super admin account bootstrapped.",
            request=request,
        )
        return user

    def invite_staff(
        self,
        *,
        invited_by: User,
        email: str,
        first_name: str,
        last_name: str,
        role: str,
        request=None,
    ) -> User:
        """Create a pending staff account and email an invitation link.

        `role` is validated against INVITABLE_STAFF_ROLES by the serializer, so
        by the time it reaches here it can only be one of the three positions
        the invite dialog offers - never SUPER_ADMIN, and never a public role.

        The invitee is created inactive with an unusable password, so the row
        exists (reserving the email) but cannot authenticate until they accept.
        `created_by` records which Super Admin issued the invitation.

        Re-inviting someone whose invitation is still pending is allowed and
        reissues the link - otherwise a lost email would strand the invitee
        behind a "user already exists" error with no way forward. A resend also
        updates the pending invitee's role, which is the only way to correct a
        mis-selected role before acceptance.
        """

        existing = User.objects.filter(email__iexact=email).first()
        user = None
        if existing is not None:
            if not self.is_pending_invite(existing):
                raise exceptions.ValidationError(
                    {"email": "A user with this email already exists."}
                )
            if not token_service.can_resend(
                user=existing, purpose=TokenPurpose.STAFF_INVITATION
            ):
                raise exceptions.ValidationError(
                    "An invitation was just sent to this email. "
                    "Please wait before resending."
                )
            user = existing

        with transaction.atomic():
            if user is None:
                user = User.objects.create_user(
                    email=email,
                    password=None,
                    first_name=first_name,
                    last_name=last_name,
                    role=role,
                    is_active=False,
                    created_by=invited_by,
                )
                # create_user() calls set_password(None), which already yields an
                # unusable password; made explicit here so the invariant survives
                # any future change to the manager.
                user.set_unusable_password()
                user.save(update_fields=["password"])
            else:
                user.first_name = first_name
                user.last_name = last_name
                user.role = role
                user.save(update_fields=["first_name", "last_name", "role"])

            _token, raw_token = token_service.issue_token(
                user=user, purpose=TokenPurpose.STAFF_INVITATION
            )
            invitee = user
            transaction.on_commit(
                lambda: self._send_staff_invitation_email(
                    user=invitee, invited_by=invited_by, raw_token=raw_token
                )
            )

        activity_service.log_auth_activity(
            user=invited_by,
            action=UserActivityActionEnums.STAFF_INVITED,
            summary=f"Invited {email} as {UserRole(role).label}.",
            request=request,
            details={"invitee_email": email, "role": role},
        )
        return user

    def accept_staff_invitation(
        self, *, email: str, token: str, password: str, request=None
    ) -> User:
        """Verify an invitation token, set the invitee's password, and activate."""

        try:
            user = User.objects.get(email__iexact=email)
        except User.DoesNotExist as exc:
            # Same message the token path uses, so probing this endpoint cannot
            # be used to enumerate which addresses have been invited.
            raise exceptions.NotFound(
                "Invalid or expired invitation link. "
                "Please ask your super admin to resend it."
            ) from exc

        token_service.verify_token(
            user=user, purpose=TokenPurpose.STAFF_INVITATION, token=token
        )
        validate_password(password, user=user)

        with transaction.atomic():
            user.set_password(password)
            user.is_active = True
            user.status = AccountStatus.ACTIVE
            user.save(update_fields=["password", "is_active", "status"])
            activity_service.log_auth_activity(
                user=user,
                action=UserActivityActionEnums.STAFF_INVITATION_ACCEPTED,
                summary="Staff invitation accepted.",
                request=request,
            )

        return user

    def list_staff(self):
        """Every staff account, newest first, for the Teams page.

        Includes pending invitees (inactive, never accepted) alongside active
        staff - the Teams page shows both, and callers tell them apart via the
        serializer's `invitation_status`.

        Annotates `has_open_invitation` so the serializer can separate "invited,
        still waiting" from "invitation withdrawn" without issuing a token query
        per row.
        """

        return (
            User.objects.filter(role__in=STAFF_ROLES)
            .select_related("created_by")
            .annotate(has_open_invitation=Exists(_open_invitation_subquery()))
            .order_by("-created_datetime")
        )

    def revoke_staff(self, *, actor: User, staff: User, request=None) -> User:
        """Withdraw a pending invitation, or deactivate an active staff member.

        Both are one operation from the Teams page's point of view ("remove this
        person"), and both are reversible rather than destructive: the row is
        kept so activity logs, `created_by` references, and any authored courses
        keep pointing at a real user.

        Revoking invalidates any outstanding invitation token, so a pending
        invitee who still has the email cannot accept afterwards.
        """

        self._assert_manageable(actor=actor, staff=staff, verb="revoke")

        # A pending invitee is also is_active=False, so "not active" alone would
        # reject exactly the case this endpoint exists to handle - withdrawing an
        # invitation before it is accepted. Only reject when there is nothing
        # left to revoke: inactive AND holding no open invitation.
        if not staff.is_active and not self.has_open_invitation(staff):
            raise exceptions.ValidationError("This staff member is already inactive.")

        with transaction.atomic():
            staff.is_active = False
            staff.status = AccountStatus.DEACTIVATED
            staff.save(update_fields=["is_active", "status"])
            token_service.invalidate_tokens(
                user=staff, purpose=TokenPurpose.STAFF_INVITATION
            )
            activity_service.log_auth_activity(
                user=actor,
                action=UserActivityActionEnums.STAFF_REVOKED,
                summary=f"Revoked staff access for {staff.email}.",
                request=request,
                details={"staff_email": staff.email, "role": staff.role},
            )

        return staff

    def reactivate_staff(self, *, actor: User, staff: User, request=None) -> User:
        """Restore a previously revoked staff member's access.

        A revoked invitee who never set a password cannot simply be switched
        back on - they would be active with an unusable password and no way in.
        Those are rejected with a pointer to re-invite instead.
        """

        self._assert_manageable(actor=actor, staff=staff, verb="reactivate")

        if staff.is_active:
            raise exceptions.ValidationError("This staff member is already active.")

        if not staff.has_usable_password():
            raise exceptions.ValidationError(
                "This invitation was revoked before it was accepted. "
                "Send a fresh invitation instead."
            )

        with transaction.atomic():
            staff.is_active = True
            staff.status = AccountStatus.ACTIVE
            staff.save(update_fields=["is_active", "status"])
            activity_service.log_auth_activity(
                user=actor,
                action=UserActivityActionEnums.STAFF_REACTIVATED,
                summary=f"Reactivated staff access for {staff.email}.",
                request=request,
                details={"staff_email": staff.email, "role": staff.role},
            )

        return staff

    @staticmethod
    def is_pending_invite(user: User) -> bool:
        """True if `user` is staff who were invited but never accepted."""

        return (
            user.role in STAFF_ROLES
            and not user.is_active
            and not user.has_usable_password()
        )

    @staticmethod
    def has_open_invitation(user: User) -> bool:
        """True if `user` holds an invitation token that has not been consumed.

        Reads the `has_open_invitation` annotation from `list_staff` when it is
        present, so rendering a roster costs one query rather than one per row,
        and falls back to a direct lookup for single-object responses.
        """

        annotated = getattr(user, "has_open_invitation", None)
        if annotated is not None:
            return bool(annotated)
        return _open_invitation_subquery(user_ref=user.pk).exists()

    @staticmethod
    def _assert_manageable(*, actor: User, staff: User, verb: str) -> None:
        """Guard the ways staff management could lock the platform out."""

        if staff.id == actor.id:
            raise exceptions.ValidationError(f"You cannot {verb} your own account.")

        if staff.role == UserRole.SUPER_ADMIN:
            # The seat is unique and bootstrap-only, so a revoked Super Admin
            # could not be replaced through the API.
            raise exceptions.ValidationError(
                f"The super admin account cannot be {verb}d."
            )

        if staff.role not in STAFF_ROLES:
            raise exceptions.ValidationError("This user is not a staff member.")

    @staticmethod
    def _send_staff_invitation_email(
        *, user: User, invited_by: User, raw_token: str
    ) -> None:
        link = build_verification_link(
            path="/accept-invitation", email=user.email, token=raw_token
        )
        result = send_templated_email_task.delay(
            receivers=[user.email],
            subject=STAFF_INVITATION_SUBJECT,
            template_name="emails/staff_invitation",
            context={
                "first_name": user.first_name,
                "invited_by_name": invited_by.get_full_name() or invited_by.email,
                "role_label": UserRole(user.role).label,
                "invitation_link": link,
                "expiry_minutes": settings.EMAIL_TOKEN_EXPIRY_MINUTES,
            },
            email_type="STAFF_INVITATION",
        )
        logger.info(
            "auth_email_queued email_type=STAFF_INVITATION recipients=%s "
            "subject=%s task_id=%s",
            [user.email],
            STAFF_INVITATION_SUBJECT,
            result.id,
        )

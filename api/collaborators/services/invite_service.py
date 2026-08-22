import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework import exceptions

from api.collaborators.enums import (
    CollaboratorInviteStatus,
    CollaboratorRole,
)
from api.collaborators.models import CollaboratorInvite, CourseCollaborator
from api.courses.models import Course, Module
from api.notification.models import Notification
from api.users.models import User

logger = logging.getLogger(__name__)

INVITE_EXPIRY_DAYS = 14


def invite_is_expired(invite: CollaboratorInvite) -> bool:
    """Whether a PENDING invite has lapsed past its expires_at."""

    return (
        invite.status == CollaboratorInviteStatus.PENDING
        and invite.expires_at <= timezone.now()
    )


def _validate_invite_targets(
    *, course: Course, email: str, role: str, assigned_modules: list[Module] | None
) -> None:
    """Shared validation for creating an invite.

    The course's own creator can't be invited (they're the Author);
    assigned modules must belong to this course; and someone who is
    already an active collaborator doesn't need an invite. An email that
    belongs to no account is fine - they can sign up later and accept.
    """

    creator_email = course.creator.email
    if email.strip().lower() == creator_email.lower():
        raise exceptions.ValidationError(
            "The course creator is already the Author and can't be invited as a collaborator."
        )
    if assigned_modules and any(
        module.course_id != course.id for module in assigned_modules
    ):
        raise exceptions.ValidationError(
            "assigned_modules must all belong to the course being invited onto."
        )
    existing_user = User.objects.filter(email__iexact=email).first()
    if existing_user and CourseCollaborator.objects.filter(
        course=course, user=existing_user
    ).exists():
        raise exceptions.ValidationError(
            "This user is already a collaborator on this course."
        )


@transaction.atomic
def create_invite(
    *,
    course: Course,
    inviter: User,
    email: str,
    role: str,
    assigned_modules: list[Module] | None = None,
) -> CollaboratorInvite:
    """Create a PENDING invite for `email` to join `course`.

    Re-inviting an email that already has a PENDING invite for this course
    supersedes it: the old one is marked REVOKED so the fresh token and any
    changed role/module assignment are the only live offer. Notifies the
    invitee in-app when their email maps to an account; otherwise the
    notification waits until they sign up.
    """

    if role == CollaboratorRole.ADMIN:
        # An admin gets full-course access; carrying module restrictions
        # would be dead data that silently changes meaning on demotion.
        assigned_modules = None

    _validate_invite_targets(
        course=course,
        email=email,
        role=role,
        assigned_modules=assigned_modules,
    )

    # Supersede any prior pending invite for the same course+email - the
    # partial unique index allows only one PENDING row per pair anyway.
    CollaboratorInvite.objects.select_for_update().filter(
        course=course,
        email__iexact=email,
        status=CollaboratorInviteStatus.PENDING,
    ).update(status=CollaboratorInviteStatus.REVOKED)

    invite = CollaboratorInvite.objects.create(
        course=course,
        email=email.strip().lower(),
        role=role,
        invited_by=inviter,
        expires_at=timezone.now() + timedelta(days=INVITE_EXPIRY_DAYS),
    )
    if assigned_modules:
        invite.assigned_modules.set(assigned_modules)

    invitee = User.objects.filter(email__iexact=email).first()
    if invitee:
        Notification.emit_in_app_notification(
            receivers=[invitee],
            title="Course collaboration invite",
            content=(
                f"{inviter.get_full_name() or inviter.email} invited you to "
                f"collaborate on '{course.title}'."
            ),
            metadata={"course_id": course.id, "invite_id": str(invite.id)},
        )
    else:
        logger.info(
            "Collaborator invite created for email without an account: %s (course=%s)",
            invite.email,
            course.id,
        )

    return invite


def _get_actionable_invite(
    *, invite: CollaboratorInvite, user: User
) -> CollaboratorInvite:
    """Guards shared by accept/decline: the signed-in user must control the
    invite's email, and the invite must still be open."""

    if user.email.lower() != invite.email.lower():
        raise exceptions.PermissionDenied(
            "This invite was sent to a different email address."
        )
    if invite.status != CollaboratorInviteStatus.PENDING:
        raise exceptions.ValidationError(
            f"This invite is no longer open (status: {invite.status})."
        )
    if invite_is_expired(invite):
        raise exceptions.ValidationError(
            "This invite has expired. Ask the course owner to send a new one."
        )
    if CourseCollaborator.objects.filter(
        course=invite.course, user=user
    ).exists():
        raise exceptions.ValidationError(
            "You are already a collaborator on this course."
        )
    return invite


@transaction.atomic
def accept_invite(*, invite: CollaboratorInvite, user: User) -> CourseCollaborator:
    """Accept a PENDING invite as `user`, creating the collaborator grant.

    Copies the invited role and module assignment onto the new
    CourseCollaborator and notifies the inviter that their offer landed.
    """

    _get_actionable_invite(invite=invite, user=user)

    collaborator = CourseCollaborator.objects.create(
        course=invite.course,
        user=user,
        role=invite.role,
        invited_by=invite.invited_by,
    )
    if invite.role == CollaboratorRole.COLLABORATOR:
        collaborator.assigned_modules.set(invite.assigned_modules.all())

    invite.status = CollaboratorInviteStatus.ACCEPTED
    invite.responded_at = timezone.now()
    invite.save(update_fields=["status", "responded_at", "updated_datetime"])

    if invite.invited_by:
        Notification.emit_in_app_notification(
            receivers=[invite.invited_by],
            title="Invite accepted",
            content=f"{user.get_full_name() or user.email} accepted your invitation to '{invite.course.title}'.",
            metadata={
                "course_id": invite.course_id,
                "invite_id": str(invite.id),
                "collaborator_id": str(collaborator.id),
            },
        )

    return collaborator


@transaction.atomic
def decline_invite(*, invite: CollaboratorInvite, user: User) -> CollaboratorInvite:
    """Decline a PENDING invite as `user`. Notifies the inviter."""

    _get_actionable_invite(invite=invite, user=user)

    invite.status = CollaboratorInviteStatus.DECLINED
    invite.responded_at = timezone.now()
    invite.save(update_fields=["status", "responded_at", "updated_datetime"])

    if invite.invited_by:
        Notification.emit_in_app_notification(
            receivers=[invite.invited_by],
            title="Invite declined",
            content=f"{user.get_full_name() or user.email} declined your invitation to '{invite.course.title}'.",
            metadata={"course_id": invite.course_id, "invite_id": str(invite.id)},
        )

    return invite


@transaction.atomic
def revoke_invite(*, invite: CollaboratorInvite) -> CollaboratorInvite:
    """Cancel a PENDING invite (inviter-side action; manage access is checked
    by the view before calling here). Accepted/declined invites are final -
    revoking history would misrepresent what happened."""

    if invite.status != CollaboratorInviteStatus.PENDING:
        raise exceptions.ValidationError(
            f"Only pending invites can be revoked (status: {invite.status})."
        )
    invite.status = CollaboratorInviteStatus.REVOKED
    invite.save(update_fields=["status", "updated_datetime"])
    return invite

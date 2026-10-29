"""Plain-function test object builder for CourseCollaborator (no factory_boy
dependency), matching the style of api.courses.tests.factories."""

from api.collaborators.enums import CollaboratorRole
from api.collaborators.models import CourseCollaborator
from api.courses.tests.factories import make_draft_course, make_user


def make_collaborator(*, course=None, user=None, **kwargs):
    defaults = {
        "course": course or make_draft_course(),
        "user": user or make_user(),
        "role": CollaboratorRole.COLLABORATOR,
    }
    defaults.update(kwargs)
    return CourseCollaborator.objects.create(**defaults)


def make_invite(*, course=None, inviter=None, email=None, **kwargs):
    """Build a CollaboratorInvite directly (bypassing invite_service) so
    tests can arrange specific lifecycle states without API round-trips."""
    from datetime import timedelta
    from uuid import uuid4

    from django.utils import timezone

    from api.collaborators.models import CollaboratorInvite
    from api.collaborators.services import invite_service

    defaults = {
        "course": course or make_draft_course(),
        "email": email or f"invitee-{uuid4().hex[:8]}@example.com",
        "invited_by": inviter or make_user(),
        "expires_at": timezone.now()
        + timedelta(days=invite_service.INVITE_EXPIRY_DAYS),
    }
    defaults.update(kwargs)
    return CollaboratorInvite.objects.create(**defaults)

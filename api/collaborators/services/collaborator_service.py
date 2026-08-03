from django.db import IntegrityError
from django.db.models import Q, QuerySet
from rest_framework import exceptions

from api.collaborators.enums import CollaboratorRole
from api.collaborators.models import CourseCollaborator
from api.courses.models import Course
from api.notification.models import Notification
from api.users.models import User


def get_courses_accessible_to(user: User) -> QuerySet[Course]:
    """Courses `user` can view/edit: the ones they created, plus any they
    collaborate on. Single source of truth for course-scoped view querysets,
    replacing plain creator=user filters wherever collaborators should now
    also be let through."""

    return Course.objects.filter(
        Q(creator=user) | Q(collaborators__user=user)
    ).distinct()


def has_manage_access(*, course: Course, user: User) -> bool:
    """Whether `user` can invite/remove/change-role for course's collaborators:
    the course's own creator, or an Admin-role collaborator."""

    if course.creator_id == user.id:
        return True
    return CourseCollaborator.objects.filter(
        course=course, user=user, role=CollaboratorRole.ADMIN
    ).exists()


def invite_collaborator(
    *, course: Course, inviter: User, email: str, role: str
) -> CourseCollaborator:
    """Grant an existing User access to `course`. Raises ValidationError if no
    account exists for `email` (no pending/token flow - see plan), the email
    belongs to the course's own creator, or they're already a collaborator."""

    try:
        user = User.objects.get(email__iexact=email)
    except User.DoesNotExist as exc:
        raise exceptions.ValidationError(
            "No account exists for this email. Ask them to sign up first."
        ) from exc

    if user.id == course.creator_id:
        raise exceptions.ValidationError(
            "The course creator is already the Author and can't be invited as a collaborator."
        )

    try:
        collaborator = CourseCollaborator.objects.create(
            course=course, user=user, role=role, invited_by=inviter
        )
    except IntegrityError as exc:
        raise exceptions.ValidationError(
            "This user is already a collaborator on this course."
        ) from exc

    Notification.emit_in_app_notification(
        receivers=[user],
        title="Added as a collaborator",
        content=f"You've been added as a collaborator on '{course.title}'.",
        metadata={"course_id": course.id},
    )

    return collaborator


def remove_collaborator(*, collaborator: CourseCollaborator) -> None:
    collaborator.delete()


def update_collaborator_role(
    *, collaborator: CourseCollaborator, role: str
) -> CourseCollaborator:
    collaborator.role = role
    collaborator.save(update_fields=["role", "updated_datetime"])
    return collaborator

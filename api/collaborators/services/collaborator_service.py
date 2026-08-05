from django.db import IntegrityError
from django.db.models import Q, QuerySet
from rest_framework import exceptions

from api.collaborators.enums import CollaboratorRole
from api.collaborators.models import CourseCollaborator
from api.courses.models import Course, Module
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
    """Whether `user` can invite/remove/change-role for course's collaborators,
    and structural (add/delete module) changes: the course's own creator, or
    an Admin-role collaborator."""

    if course.creator_id == user.id:
        return True
    return CourseCollaborator.objects.filter(
        course=course, user=user, role=CollaboratorRole.ADMIN
    ).exists()


def get_modules_accessible_to(*, user: User, course_id) -> QuerySet[Module]:
    """Modules under `course_id` that `user` may view/edit (SCCS PRD Section
    14): every module for the course's creator or an ADMIN-role
    collaborator, only explicitly assigned modules for a plain COLLABORATOR.
    Returns an empty queryset - not a 404 - for a course the user can't
    access at all, matching this app's "list never 404s" convention."""

    try:
        course = Course.objects.get(pk=course_id)
    except Course.DoesNotExist:
        return Module.objects.none()

    if course.creator_id == user.id:
        return course.modules.all()

    try:
        collaborator = CourseCollaborator.objects.get(course=course, user=user)
    except CourseCollaborator.DoesNotExist:
        return Module.objects.none()

    if collaborator.role == CollaboratorRole.ADMIN:
        return course.modules.all()
    return collaborator.assigned_modules.all()


def can_access_module(*, user: User, module: Module) -> bool:
    """Whether `user` may view/edit `module`: the course creator, an
    ADMIN-role collaborator (full-course access), or a plain COLLABORATOR
    explicitly assigned this module."""

    return get_modules_accessible_to(user=user, course_id=module.course_id).filter(
        pk=module.pk
    ).exists()


def invite_collaborator(
    *,
    course: Course,
    inviter: User,
    email: str,
    role: str,
    assigned_modules: list[Module] | None = None,
) -> CourseCollaborator:
    """Grant an existing User access to `course`. Raises ValidationError if no
    account exists for `email` (no pending/token flow - see plan), the email
    belongs to the course's own creator, they're already a collaborator, or
    `assigned_modules` includes a module that doesn't belong to `course`."""

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
    if assigned_modules and any(m.course_id != course.id for m in assigned_modules):
        raise exceptions.ValidationError(
            "assigned_modules must all belong to the course being invited onto."
        )

    try:
        collaborator = CourseCollaborator.objects.create(
            course=course, user=user, role=role, invited_by=inviter
        )
    except IntegrityError as exc:
        raise exceptions.ValidationError(
            "This user is already a collaborator on this course."
        ) from exc

    if assigned_modules:
        collaborator.assigned_modules.set(assigned_modules)

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
    *,
    collaborator: CourseCollaborator,
    role: str | None = None,
    assigned_modules: list[Module] | None = None,
) -> CourseCollaborator:
    """Update a collaborator's role and/or module assignment. Either kwarg
    may be omitted (e.g. a role-only or assignment-only PATCH)."""

    if assigned_modules is not None and any(
        m.course_id != collaborator.course_id for m in assigned_modules
    ):
        raise exceptions.ValidationError(
            "assigned_modules must all belong to the collaborator's course."
        )

    if role is not None:
        collaborator.role = role
        collaborator.save(update_fields=["role", "updated_datetime"])
    if assigned_modules is not None:
        collaborator.assigned_modules.set(assigned_modules)

    return collaborator

from collections.abc import Iterable

from django.db.models import Q, QuerySet
from rest_framework import exceptions

from api.collaborators.enums import CollaboratorRole
from api.collaborators.models import CourseCollaborator, WorkspaceCollaborator
from api.courses.models import Course, Module
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

    return (
        get_modules_accessible_to(user=user, course_id=module.course_id)
        .filter(pk=module.pk)
        .exists()
    )


def accept_workspace_invite(
    *, invite: WorkspaceCollaborator, user: User
) -> WorkspaceCollaborator:
    """Link an accepted workspace invite to `user`'s account and mark it
    ACTIVE. The single acceptance step shared by the signup auto-accept
    signal (signing up with the invited email means they accepted) and the
    /workspace-collaborators/{id}/accept/ endpoint for people who already
    had an account when the invite was sent. Notifies the workspace owner."""

    if invite.status != WorkspaceCollaborator.Status.PENDING:
        raise exceptions.ValidationError(
            f"This invite is no longer open (status: {invite.status})."
        )

    invite.user_id = user.id
    invite.status = WorkspaceCollaborator.Status.ACTIVE
    invite.save(update_fields=["user_id", "status", "updated_datetime"])

    from api.notification.models import Notification

    Notification.emit_in_app_notification(
        receivers=[invite.owner],
        title="Workspace invite accepted",
        content=(
            f"{user.get_full_name() or user.email} accepted your "
            "invitation to join your workspace."
        ),
        metadata={"workspace_collaborator_id": str(invite.id)},
    )

    return invite


def get_unassigned_workspace_members(owner: User) -> QuerySet[WorkspaceCollaborator]:
    """ACTIVE members of `owner`'s workspace roster with no assignment on any
    course `owner` created. Accepting a workspace invite grants no course
    access, so without these the global Collaborators list would miss
    people who have joined the team but aren't on a course yet. Members
    who do have an assignment are excluded - they already appear as their
    course rows."""

    return (
        WorkspaceCollaborator.objects.filter(
            owner=owner,
            status=WorkspaceCollaborator.Status.ACTIVE,
            user__isnull=False,
        )
        .exclude(user__course_collaborations__course__creator=owner)
        .select_related("user")
    )


def merge_collaborator_rows(
    course_rows: Iterable[CourseCollaborator],
    workspace_rows: Iterable[WorkspaceCollaborator],
) -> list[CourseCollaborator | WorkspaceCollaborator]:
    """Combine course assignments and unassigned workspace members, newest
    first - the same -created_datetime order both models default to."""

    return sorted(
        [*course_rows, *workspace_rows],
        key=lambda row: row.created_datetime,
        reverse=True,
    )


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

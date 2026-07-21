from rest_framework.permissions import BasePermission

from api.users.enums import UserRole


class HasRole(BasePermission):
    """Base permission that grants access when the requesting user's role is allowed.

    Superusers are always granted access regardless of `allowed_roles`, matching
    Django's convention that is_superuser bypasses ordinary authorization checks.
    Subclass and set `allowed_roles` to a tuple of `UserRole` values.
    """

    allowed_roles: tuple = ()

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        return bool(user.is_superuser or user.role in self.allowed_roles)


class IsCourseCreatorRole(HasRole):
    """Grants access to users who author courses.

    Covers both the public Course Creator and the invited Writer - they do the
    same job, so they share one permission rather than duplicating every
    authoring endpoint.
    """

    allowed_roles = (UserRole.COURSE_CREATOR, UserRole.STAFF_WRITER)


class IsCreatorReviewerRole(HasRole):
    """Grants access to users who review submitted courses.

    Covers the Creator Reviewer and the invited Verifier.
    """

    allowed_roles = (UserRole.CREATOR_REVIEWER, UserRole.STAFF_VERIFIER)


class IsAdminRole(HasRole):
    """Grants access to the platform's administrative tier.

    Covers Admins, invited Approvers (whose job is exactly the admin approval
    step), and Super Admins - who outrank Admins, so anything an Admin can do a
    Super Admin can do too.
    """

    allowed_roles = (
        UserRole.ADMIN,
        UserRole.STAFF_APPROVER,
        UserRole.SUPER_ADMIN,
    )


class CanManageCategories(HasRole):
    """Grants create/update/delete on course categories.

    Deliberately NOT `IsCourseCreatorRole`: that class also covers the public
    COURSE_CREATOR role, and category management is a staff responsibility -
    public creators only browse categories to pick one for a course.

    Note this excludes Admins and Approvers, who keep read access like everyone
    else. Categories carry `creator_price`, so a Writer editing one can affect
    what their own submissions pay; that is an accepted trust decision for
    salaried staff rather than an oversight.
    """

    allowed_roles = (UserRole.STAFF_WRITER, UserRole.SUPER_ADMIN)


class IsSuperAdminRole(HasRole):
    """Grants access to users with the Super Admin role only.

    Use this for platform-ownership operations - inviting and revoking staff,
    and any future settings an ordinary Admin must not be able to change.
    """

    allowed_roles = (UserRole.SUPER_ADMIN,)

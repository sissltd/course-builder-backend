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
    """Grants access to users with the Course Creator role."""

    allowed_roles = (UserRole.COURSE_CREATOR,)


class IsCreatorReviewerRole(HasRole):
    """Grants access to users with the Creator Reviewer role."""

    allowed_roles = (UserRole.CREATOR_REVIEWER,)


class IsAdminRole(HasRole):
    """Grants access to users with the Admin role."""

    allowed_roles = (UserRole.ADMIN,)

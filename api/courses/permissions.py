from rest_framework.permissions import BasePermission

from api.courses.models import Course

# Actions where a collaborator (not just the creator) is granted access -
# never destroy/submit, which stay creator-only (see CourseViewSet.get_queryset).
COLLABORATOR_ALLOWED_ACTIONS = {"retrieve", "update", "partial_update"}


class IsCourseOwner(BasePermission):
    """Object-level permission granting access to the course's creator, and
    to any collaborator for the view/edit-safe actions above.

    Resolves the owning Course whether `obj` is a Course itself, or a child
    object exposing `.course` (Module) or `.module.course` (Lesson).
    """

    def has_object_permission(self, request, view, obj):
        course = self._resolve_course(obj)
        if not course:
            return False
        if course.creator_id == request.user.id:
            return True
        if getattr(view, "action", None) not in COLLABORATOR_ALLOWED_ACTIONS:
            return False

        # Local import: keeps this permission module decoupled from the
        # collaborators app at import time, mirroring the pattern used
        # elsewhere in this app for one-directional runtime-only dependencies.
        from api.collaborators.services import collaborator_service

        return (
            collaborator_service.get_courses_accessible_to(request.user)
            .filter(pk=course.pk)
            .exists()
        )

    @staticmethod
    def _resolve_course(obj):
        if isinstance(obj, Course):
            return obj
        course = getattr(obj, "course", None)
        if course is not None:
            return course
        module = getattr(obj, "module", None)
        return getattr(module, "course", None) if module else None


class HasCompletedOnboarding(BasePermission):
    """Blocks course creation until the creator has finished the onboarding
    wizard and, if the platform's creator-agreement policy version has since
    changed, re-accepted it.

    Superusers bypass, matching HasRole's convention elsewhere
    (api/users/permissions.py). A read-only lookup - unlike
    creator_profile_service.get_or_create_profile, this never creates a
    CreatorProfile row as a side effect of merely checking a permission.
    """

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if user.is_superuser:
            return True

        # Local import: keeps this permission module decoupled from the
        # onboarding app at import time, mirroring the pattern used above
        # for the collaborators app.
        from api.onboarding.models import CreatorProfile

        profile = CreatorProfile.objects.filter(user=user).first()
        if profile is None or not profile.has_completed_onboarding:
            self.message = "You must complete onboarding before creating a course."
            return False
        if profile.needs_policy_reacceptance:
            self.message = (
                "The creator agreement has been updated - please re-accept "
                "it before creating a course."
            )
            return False
        return True

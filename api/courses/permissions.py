from rest_framework.permissions import BasePermission

from api.courses.models import Course


class IsCourseOwner(BasePermission):
    """Object-level permission granting access only to the course's creator.

    Resolves the owning Course whether `obj` is a Course itself, or a child
    object exposing `.course` (Module) or `.module.course` (Lesson).
    """

    def has_object_permission(self, request, view, obj):
        course = self._resolve_course(obj)
        return bool(course and course.creator_id == request.user.id)

    @staticmethod
    def _resolve_course(obj):
        if isinstance(obj, Course):
            return obj
        course = getattr(obj, "course", None)
        if course is not None:
            return course
        module = getattr(obj, "module", None)
        return getattr(module, "course", None) if module else None

from rest_framework import exceptions
from rest_framework.viewsets import ModelViewSet

from api.courses.enums import CourseStatus
from api.courses.models import Lesson, Module
from api.courses.serializers import LessonSerializer, LessonWriteSerializer
from api.users.permissions import IsCourseCreatorRole


class LessonViewSet(ModelViewSet):
    """Sub-resource CRUD for Lessons nested under a Draft course's module."""

    permission_classes = [IsCourseCreatorRole]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Lesson.objects.none()
        return Lesson.objects.filter(
            module_id=self.kwargs["module_pk"],
            module__course_id=self.kwargs["course_pk"],
            module__course__creator=self.request.user,
        )

    def get_serializer_class(self):
        if self.action in {"list", "retrieve"}:
            return LessonSerializer
        return LessonWriteSerializer

    def _get_module(self) -> Module:
        try:
            return Module.objects.select_related("course").get(
                pk=self.kwargs["module_pk"],
                course_id=self.kwargs["course_pk"],
                course__creator=self.request.user,
            )
        except Module.DoesNotExist as exc:
            raise exceptions.NotFound("Module not found.") from exc

    def perform_create(self, serializer):
        module = self._get_module()
        if module.course.status != CourseStatus.DRAFT:
            raise exceptions.ValidationError(
                "Lessons can only be added while the course is Draft."
            )
        serializer.save(
            module=module, created_by=self.request.user, updated_by=self.request.user
        )

    def perform_update(self, serializer):
        if serializer.instance.module.course.status != CourseStatus.DRAFT:
            raise exceptions.ValidationError(
                "Lessons can only be edited while the course is Draft."
            )
        serializer.save(updated_by=self.request.user)

    def perform_destroy(self, instance):
        if instance.module.course.status != CourseStatus.DRAFT:
            raise exceptions.ValidationError(
                "Lessons can only be deleted while the course is Draft."
            )
        instance.delete()

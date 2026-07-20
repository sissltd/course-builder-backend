from rest_framework import exceptions
from rest_framework.viewsets import ModelViewSet

from api.courses.enums import CourseStatus
from api.courses.models import Course, Module
from api.courses.serializers import ModuleSerializer, ModuleWriteSerializer
from api.courses.services import collaborator_service
from api.users.permissions import IsCourseCreatorRole


class ModuleViewSet(ModelViewSet):
    """Sub-resource CRUD for Modules nested under a Draft course.

    get_queryset() is filtered to the courses the requesting user can access
    (creator or collaborator) so a non-owner/non-collaborator's request 404s
    (existence is not leaked via a 403) instead of relying solely on an
    object-level permission check.
    """

    permission_classes = [IsCourseCreatorRole]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Module.objects.none()
        return Module.objects.filter(
            course_id=self.kwargs["course_pk"],
            course__in=collaborator_service.get_courses_accessible_to(
                self.request.user
            ),
        )

    def get_serializer_class(self):
        if self.action in {"list", "retrieve"}:
            return ModuleSerializer
        return ModuleWriteSerializer

    def _get_course(self) -> Course:
        try:
            return collaborator_service.get_courses_accessible_to(
                self.request.user
            ).get(pk=self.kwargs["course_pk"])
        except Course.DoesNotExist as exc:
            raise exceptions.NotFound("Course not found.") from exc

    def perform_create(self, serializer):
        course = self._get_course()
        if course.status != CourseStatus.DRAFT:
            raise exceptions.ValidationError(
                "Modules can only be added while the course is Draft."
            )
        serializer.save(
            course=course, created_by=self.request.user, updated_by=self.request.user
        )

    def perform_update(self, serializer):
        if serializer.instance.course.status != CourseStatus.DRAFT:
            raise exceptions.ValidationError(
                "Modules can only be edited while the course is Draft."
            )
        serializer.save(updated_by=self.request.user)

    def perform_destroy(self, instance):
        if instance.course.status != CourseStatus.DRAFT:
            raise exceptions.ValidationError(
                "Modules can only be deleted while the course is Draft."
            )
        instance.delete()

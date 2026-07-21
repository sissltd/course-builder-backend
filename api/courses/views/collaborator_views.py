from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from api.courses.models import Course, CourseCollaborator
from api.courses.serializers import (
    CollaboratorInviteSerializer,
    CollaboratorRoleUpdateSerializer,
    CollaboratorSerializer,
)
from api.courses.services import collaborator_service
from api.users.permissions import IsCourseCreatorRole


class CourseCollaboratorViewSet(ModelViewSet):
    """Sub-resource CRUD for a course's collaborators.

    Anyone with view access to the course (creator or any collaborator) can
    list/retrieve; only the creator or an Admin-role collaborator can
    invite/remove/change-role (checked in _require_manage_access rather than
    a declarative permission class, since manage-access depends on the
    resolved course, not just the requesting user's platform-wide role).
    """

    permission_classes = [IsCourseCreatorRole]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return CourseCollaborator.objects.none()
        return CourseCollaborator.objects.filter(
            course_id=self.kwargs["course_pk"],
            course__in=collaborator_service.get_courses_accessible_to(
                self.request.user
            ),
        ).select_related("user", "invited_by")

    def get_serializer_class(self):
        if self.action == "create":
            return CollaboratorInviteSerializer
        if self.action in {"update", "partial_update"}:
            return CollaboratorRoleUpdateSerializer
        return CollaboratorSerializer

    def _get_course(self) -> Course:
        try:
            return collaborator_service.get_courses_accessible_to(
                self.request.user
            ).get(pk=self.kwargs["course_pk"])
        except Course.DoesNotExist as exc:
            raise exceptions.NotFound("Course not found.") from exc

    def _require_manage_access(self, course: Course) -> None:
        if not collaborator_service.has_manage_access(
            course=course, user=self.request.user
        ):
            raise exceptions.PermissionDenied(
                "Only the course creator or an Admin collaborator can manage collaborators."
            )

    def create(self, request, *args, **kwargs):
        course = self._get_course()
        self._require_manage_access(course)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        collaborator = collaborator_service.invite_collaborator(
            course=course, inviter=request.user, **serializer.validated_data
        )
        return Response(
            CollaboratorSerializer(collaborator).data, status=status.HTTP_201_CREATED
        )

    def partial_update(self, request, *args, **kwargs):
        collaborator = self.get_object()
        self._require_manage_access(collaborator.course)
        serializer = self.get_serializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        collaborator = collaborator_service.update_collaborator_role(
            collaborator=collaborator, **serializer.validated_data
        )
        return Response(CollaboratorSerializer(collaborator).data)

    def destroy(self, request, *args, **kwargs):
        collaborator = self.get_object()
        self._require_manage_access(collaborator.course)
        collaborator_service.remove_collaborator(collaborator=collaborator)
        return Response(status=status.HTTP_204_NO_CONTENT)

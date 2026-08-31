from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from api.collaborators.models import CourseCollaborator
from api.collaborators.filters import CollaboratorFilter
from api.collaborators.serializers import (
    CollaboratorRoleUpdateSerializer,
    CollaboratorSerializer,
)
from api.collaborators.services import collaborator_service
from api.courses.models import Course
from api.users.permissions import IsCourseCreatorRole
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES

_COLLABORATOR_EXAMPLE = {
    "id": "b8c9d0e1-f2a3-4b4c-5d6e-7f8a9b0c1d2e",
    "name": "Jane Doe",
    "email": "jane.doe@example.com",
    "country_of_origin": "NG",
    "date_added": "2026-07-20T11:00:00.000Z",
    "role": "COLLABORATOR",
    "role_label": "Collaborator",
    "course_id": "3f9a2e11-6b7c-4d2a-9e5f-1c8d4a7b2f30",
    "course_title": "Intro to Python",
    "category": {
        "id": "7d2f4b18-3c9a-4e51-b8f0-1a6c5d3e9b74",
        "name": "Software Engineering",
    },
    "assigned_modules": [
        {
            "id": "b2c3d4e5-f6a7-4b8c-9d0e-1f2a3b4c5d6e",
            "title": "Getting Started",
            "order": 1,
        }
    ],
}

_AUTH_LINE = (
    "**Auth:** Course Creator or Writer. Read access (list/retrieve) is "
    "open to anyone with view access to the course - the creator or any "
    "existing collaborator. Write access (change-role/remove) is "
    "further restricted to the course's own creator or an Admin-role "
    "collaborator, checked against the resolved course rather than a "
    "platform-wide role."
)

_MANAGE_ACCESS_403 = OpenApiResponse(
    description="The caller can view the course but cannot manage its collaborators.",
    examples=[
        OpenApiExample(
            name="Not creator or Admin collaborator",
            value={
                "errors": [
                    {
                        "type": "client_error",
                        "code": "permission_denied",
                        "message": (
                            "Only the course creator or an Admin "
                            "collaborator can manage collaborators."
                        ),
                        "field_name": None,
                    }
                ]
            },
        ),
    ],
)


@extend_schema_view(
    list=extend_schema(
        summary="List collaborators across my courses",
        description=(
            "Returns collaborator assignments across every course owned by "
            "the caller. Supply `course_id` to narrow the result to one "
            "accessible course. Each row uses the Collaborators "
            "screen field names: `name`, `email`, `date_added`, and `role`. "
            "It also identifies the course and category for filtering and "
            "display. The same person appears once per course assignment.\n\n"
            "Called from the global Collaborators screen, or with `course_id` "
            "from a course's manage-collaborators panel.\n\n"
            f"{_AUTH_LINE}\n\n"
            "**Prerequisites:** None for the global list.\n\n"
            "**Important:** An inaccessible `course_id` returns an empty "
            "200 rather than leaking whether the course exists. The unscoped "
            "list includes only courses the caller owns, never another "
            "creator's courses on which the caller merely collaborates."
        ),
        tags=["Creator — Collaborators"],
        parameters=[
            OpenApiParameter(
                name="course_id",
                type=str,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    "Optional course UUID. Omit it to list assignments across "
                    "all courses owned by the caller."
                ),
            ),
            OpenApiParameter(
                name="category",
                type=str,
                location=OpenApiParameter.QUERY,
                description="Category UUID used by the Figma category filter.",
            ),
            OpenApiParameter(
                name="search",
                type=str,
                location=OpenApiParameter.QUERY,
                description="The Search collaborator field; matches name or email.",
            ),
            OpenApiParameter(
                name="role",
                type=str,
                location=OpenApiParameter.QUERY,
                enum=["COLLABORATOR", "ADMIN"],
                description="The Role filter.",
            ),
            OpenApiParameter(
                name="date_from",
                type=str,
                location=OpenApiParameter.QUERY,
                description="Inclusive From value for the Date range (YYYY-MM-DD).",
            ),
            OpenApiParameter(
                name="date_to",
                type=str,
                location=OpenApiParameter.QUERY,
                description="Inclusive To value for the Date range (YYYY-MM-DD).",
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=CollaboratorSerializer(many=True),
                description="Collaborator assignments across the selected courses.",
                examples=[
                    OpenApiExample(name="Success", value=[_COLLABORATOR_EXAMPLE])
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    retrieve=extend_schema(
        summary="Retrieve a collaborator",
        description=(
            "Returns a single collaborator by id.\n\n"
            "Called when opening a collaborator's row in the manage panel.\n\n"
            f"{_AUTH_LINE}\n\n"
            "**Prerequisites:** The collaborator must exist on a course "
            "visible to the caller.\n\n"
            "**Important:** A collaborator on a course the caller can't "
            "access 404s rather than 403s, so existence isn't leaked."
        ),
        tags=["Creator — Collaborators"],
        responses={
            200: OpenApiResponse(
                response=CollaboratorSerializer,
                description="The requested collaborator.",
                examples=[OpenApiExample(name="Success", value=_COLLABORATOR_EXAMPLE)],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    destroy=extend_schema(
        summary="Remove a collaborator",
        description=(
            "Removes a collaborator from a course, revoking their access "
            "immediately.\n\n"
            "Called from the 'Remove' action in the manage collaborators "
            "panel.\n\n"
            f"{_AUTH_LINE}\n\n"
            "**Prerequisites:** The caller must be the course's creator or "
            "an Admin-role collaborator on it.\n\n"
            "**Important:** There is no undo - removing a collaborator "
            "deletes the row outright rather than deactivating it. The "
            "course's own creator can never be removed this way; they "
            "aren't stored as a collaborator row at all."
        ),
        tags=["Creator — Collaborators"],
        responses={
            204: OpenApiResponse(description="Collaborator removed."),
            403: _MANAGE_ACCESS_403,
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
)
class CourseCollaboratorViewSet(ModelViewSet):
    """Flat resource for collaborators across the creator's owned courses.

    List can be narrowed via `course_id`; detail actions resolve through the
    collaborator object itself.

    New collaborators are created exclusively by accepting an invite (see
    CollaboratorInviteViewSet), so this viewset has no create action -
    only list/retrieve/PATCH role-assignment/DELETE.

    Anyone with view access to the course (creator or any collaborator) can
    list/retrieve; only the creator or an Admin-role collaborator can
    change-role/remove (checked in _require_manage_access rather than
    a declarative permission class, since manage-access depends on the
    resolved course, not just the requesting user's platform-wide role).
    """

    permission_classes = [IsCourseCreatorRole]
    http_method_names = ["get", "patch", "delete", "head", "options"]
    filterset_class = CollaboratorFilter

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return CourseCollaborator.objects.none()
        queryset = CourseCollaborator.objects.filter(
            course__in=collaborator_service.get_courses_accessible_to(
                self.request.user
            ),
        ).select_related("user", "invited_by", "course", "course__category")
        if self.action == "list":
            course_id = self.request.query_params.get("course_id")
            if course_id:
                queryset = queryset.filter(course_id=course_id)
            else:
                queryset = queryset.filter(course__creator=self.request.user)
        return queryset

    def get_serializer_class(self):
        if self.action in {"update", "partial_update"}:
            return CollaboratorRoleUpdateSerializer
        return CollaboratorSerializer

    def _require_manage_access(self, course: Course) -> None:
        if not collaborator_service.has_manage_access(
            course=course, user=self.request.user
        ):
            raise exceptions.PermissionDenied(
                "Only the course creator or an Admin collaborator can manage collaborators."
            )

    @extend_schema(
        summary="Change a collaborator's role or module assignment",
        description=(
            "Updates only the fields supplied - `role`, `assigned_modules`, "
            "or both. Used to promote a Collaborator to Admin (or demote an "
            "Admin back to Collaborator), and/or to change which modules a "
            "plain Collaborator is restricted to.\n\n"
            "Called from the role dropdown and the module-assignment picker "
            "in the manage collaborators panel.\n\n"
            f"{_AUTH_LINE}\n\n"
            "**Prerequisites:** The caller must be the course's creator or "
            "an Admin-role collaborator on it.\n\n"
            "**Important:** There is no PUT (full replace) on this "
            "resource - only PATCH, since both fields are independently "
            "optional. Sending `assigned_modules` replaces the full list, "
            "it does not merge with the existing one. `assigned_modules` is "
            "ignored in effect for an `ADMIN`-role collaborator, who always "
            "has full-course access."
        ),
        tags=["Creator — Collaborators"],
        request=CollaboratorRoleUpdateSerializer,
        examples=[
            OpenApiExample(
                name="Change role",
                request_only=True,
                value={"role": "ADMIN"},
            ),
            OpenApiExample(
                name="Change module assignment",
                request_only=True,
                value={"assigned_modules": ["b2c3d4e5-f6a7-4b8c-9d0e-1f2a3b4c5d6e"]},
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=CollaboratorSerializer,
                description="Collaborator's role updated.",
                examples=[
                    OpenApiExample(
                        name="Success",
                        value={**_COLLABORATOR_EXAMPLE, "role": "ADMIN"},
                    )
                ],
            ),
            403: _MANAGE_ACCESS_403,
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
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

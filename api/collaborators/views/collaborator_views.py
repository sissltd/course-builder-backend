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
from api.collaborators.serializers import (
    CollaboratorInviteSerializer,
    CollaboratorRoleUpdateSerializer,
    CollaboratorSerializer,
)
from api.collaborators.services import collaborator_service
from api.courses.models import Course
from api.users.permissions import IsCourseCreatorRole
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES

_COLLABORATOR_EXAMPLE = {
    "id": "b8c9d0e1-f2a3-4b4c-5d6e-7f8a9b0c1d2e",
    "user": {
        "id": "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d",
        "first_name": "Jane",
        "last_name": "Doe",
        "email": "jane.doe@example.com",
        "country": "NG",
        "sex": "FEMALE",
    },
    "role": "COLLABORATOR",
    "created_datetime": "2026-07-20T11:00:00.000Z",
}

_AUTH_LINE = (
    "**Auth:** Course Creator or Writer. Read access (list/retrieve) is "
    "open to anyone with view access to the course - the creator or any "
    "existing collaborator. Write access (invite/change-role/remove) is "
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
        summary="List a course's collaborators",
        description=(
            "Returns everyone with collaborator access to a course, "
            "identified by `course_id`. Backs the 'Manage collaborators' "
            "panel on the course builder.\n\n"
            "Called when opening a course's collaborators panel.\n\n"
            f"{_AUTH_LINE}\n\n"
            "**Prerequisites:** `course_id` is required.\n\n"
            "**Important:** Matches ModuleViewSet's own convention: `list` "
            "never calls `get_object()`, so a course the caller can't "
            "access returns an empty 200 rather than 403/404. Omitting "
            "`course_id` returns 400, not an unscoped list of every "
            "collaborator on the platform."
        ),
        tags=["Collaborators"],
        parameters=[
            OpenApiParameter(
                name="course_id",
                type=str,
                location=OpenApiParameter.QUERY,
                required=True,
                description="UUID of the course to list collaborators for.",
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=CollaboratorSerializer(many=True),
                description="Collaborators on the course.",
                examples=[OpenApiExample(name="Success", value=[_COLLABORATOR_EXAMPLE])],
            ),
            400: OpenApiResponse(
                description="`course_id` was not supplied.",
                examples=[
                    OpenApiExample(
                        name="Missing course_id",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "required",
                                    "message": "This query parameter is required.",
                                    "field_name": "course_id",
                                }
                            ]
                        },
                    ),
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
        tags=["Collaborators"],
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
        tags=["Collaborators"],
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
    """Flat CRUD resource for course collaborators (course scoped via
    `course_id` on list/create, and via the collaborator object itself on
    retrieve/update/destroy).

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
        queryset = CourseCollaborator.objects.filter(
            course__in=collaborator_service.get_courses_accessible_to(
                self.request.user
            ),
        ).select_related("user", "invited_by")
        if self.action == "list":
            course_id = self.request.query_params.get("course_id")
            if not course_id:
                raise exceptions.ValidationError(
                    {"course_id": "This query parameter is required."}
                )
            queryset = queryset.filter(course_id=course_id)
        return queryset

    def get_serializer_class(self):
        if self.action == "create":
            return CollaboratorInviteSerializer
        if self.action in {"update", "partial_update"}:
            return CollaboratorRoleUpdateSerializer
        return CollaboratorSerializer

    def _get_course(self, course_id) -> Course:
        try:
            return collaborator_service.get_courses_accessible_to(
                self.request.user
            ).get(pk=course_id)
        except Course.DoesNotExist as exc:
            raise exceptions.NotFound("Course not found.") from exc

    def _require_manage_access(self, course: Course) -> None:
        if not collaborator_service.has_manage_access(
            course=course, user=self.request.user
        ):
            raise exceptions.PermissionDenied(
                "Only the course creator or an Admin collaborator can manage collaborators."
            )

    @extend_schema(
        summary="Invite a collaborator to a course",
        description=(
            "Grants an existing user access to a course by email, and "
            "notifies them in-app. There is no pending/token invite flow - "
            "the invitee must already have an account.\n\n"
            "Called from the 'Invite collaborator' action in the manage "
            "collaborators panel.\n\n"
            f"{_AUTH_LINE}\n\n"
            "**Prerequisites:** The caller must be the course's creator or "
            "an Admin-role collaborator on it; the invitee must already "
            "have an account and not already be a collaborator or the "
            "course's own creator.\n\n"
            "**Important:** `role` defaults to `COLLABORATOR` if omitted - "
            "pass `ADMIN` to grant the invitee manage access too."
        ),
        tags=["Collaborators"],
        request=CollaboratorInviteSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={
                    "course_id": "3f9a2e11-6b7c-4d2a-9e5f-1c8d4a7b2f30",
                    "email": "jane.doe@example.com",
                    "role": "COLLABORATOR",
                },
            ),
        ],
        responses={
            201: OpenApiResponse(
                response=CollaboratorSerializer,
                description="Collaborator added.",
                examples=[OpenApiExample(name="Success", value=_COLLABORATOR_EXAMPLE)],
            ),
            400: OpenApiResponse(
                description=(
                    "No account exists for that email, the email belongs to "
                    "the course's own creator, or they're already a "
                    "collaborator."
                ),
                examples=[
                    OpenApiExample(
                        name="No account for email",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": (
                                        "No account exists for this email. "
                                        "Ask them to sign up first."
                                    ),
                                    "field_name": None,
                                }
                            ]
                        },
                    ),
                    OpenApiExample(
                        name="Already the creator",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": (
                                        "The course creator is already the "
                                        "Author and can't be invited as a "
                                        "collaborator."
                                    ),
                                    "field_name": None,
                                }
                            ]
                        },
                    ),
                    OpenApiExample(
                        name="Already a collaborator",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": (
                                        "This user is already a collaborator "
                                        "on this course."
                                    ),
                                    "field_name": None,
                                }
                            ]
                        },
                    ),
                ],
            ),
            403: _MANAGE_ACCESS_403,
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated_data = dict(serializer.validated_data)
        course = self._get_course(validated_data.pop("course_id"))
        self._require_manage_access(course)
        collaborator = collaborator_service.invite_collaborator(
            course=course, inviter=request.user, **validated_data
        )
        return Response(
            CollaboratorSerializer(collaborator).data, status=status.HTTP_201_CREATED
        )

    @extend_schema(
        summary="Change a collaborator's role",
        description=(
            "Updates only the fields supplied - in practice, just `role` - "
            "the way to promote a Collaborator to Admin or demote an "
            "Admin back to Collaborator.\n\n"
            "Called from the role dropdown in the manage collaborators "
            "panel.\n\n"
            f"{_AUTH_LINE}\n\n"
            "**Prerequisites:** The caller must be the course's creator or "
            "an Admin-role collaborator on it.\n\n"
            "**Important:** There is no PUT (full replace) on this "
            "resource - only PATCH, since `role` is the only field a "
            "manager can ever change."
        ),
        tags=["Collaborators"],
        request=CollaboratorRoleUpdateSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={"role": "ADMIN"},
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

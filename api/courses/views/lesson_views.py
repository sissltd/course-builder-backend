from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from rest_framework import exceptions
from rest_framework.viewsets import ModelViewSet

from api.collaborators.services import collaborator_service
from api.courses.enums import CourseStatus
from api.courses.models import Lesson, Module
from api.courses.serializers import LessonSerializer, LessonWriteSerializer
from api.courses.services import course_service, module_lock_service
from api.users.permissions import IsCourseCreatorRole
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES

_LESSON_EXAMPLE = {
    "id": "c3d4e5f6-a7b8-4c9d-0e1f-2a3b4c5d6e7f",
    "title": "Variables and Data Types",
    "order": 1,
    "script": "In this lesson we cover Python's core data types...",
    "video_url": "https://example.com/lessons/variables.mp4",
    "learning_objectives": ["Identify Python's built-in data types"],
    "duration_minutes": 15,
    "assessment": None,
}

_PATH_PARAMETERS = [
    OpenApiParameter(
        name="course_pk",
        type=str,
        location=OpenApiParameter.PATH,
        description="UUID of the parent course.",
    ),
    OpenApiParameter(
        name="module_pk",
        type=str,
        location=OpenApiParameter.PATH,
        description="UUID of the parent module.",
    ),
]

_DRAFT_ONLY_400 = OpenApiResponse(
    description="The parent course is not Draft.",
    examples=[
        OpenApiExample(
            name="Course not editable",
            value={
                "errors": [
                    {
                        "type": "validation_error",
                        "code": "invalid",
                        "message": "Lessons can only be edited while the course is Draft.",
                        "field_name": None,
                    }
                ]
            },
        ),
    ],
)

_MODULE_LOCKED_423 = OpenApiResponse(
    description=(
        "The parent module is currently locked for editing by another user "
        "(see `POST .../modules/{module_pk}/lock/`)."
    ),
    examples=[
        OpenApiExample(
            name="Module locked",
            value={
                "errors": [
                    {
                        "type": "client_error",
                        "code": "locked",
                        "message": "This module is currently being edited by another user.",
                        "field_name": None,
                    }
                ]
            },
        ),
    ],
)


@extend_schema_view(
    list=extend_schema(
        summary="List a module's lessons",
        description=(
            "Returns every lesson in the module, each with its assessment "
            "nested inline if one is set.\n\n"
            "Called when expanding a module in the course builder.\n\n"
            "**Auth:** Course Creator/Writer with access to the course.\n\n"
            "**Prerequisites:** None beyond having access to the course.\n\n"
            "**Important:** Like ModuleViewSet, `list` never 404s on a "
            "module the caller can't reach - it returns an empty result "
            "set instead."
        ),
        tags=["Creator — Lessons"],
        parameters=_PATH_PARAMETERS,
        responses={
            200: OpenApiResponse(
                response=LessonSerializer(many=True),
                description="Lessons, in whatever order the queryset returns them.",
                examples=[OpenApiExample(name="Success", value=[_LESSON_EXAMPLE])],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    retrieve=extend_schema(
        summary="Retrieve a lesson",
        description=(
            "Returns a single lesson with its assessment.\n\n"
            "Called when opening a lesson in the course builder.\n\n"
            "**Auth:** Course Creator/Writer with access to the course.\n\n"
            "**Prerequisites:** The lesson must exist under the given "
            "module.\n\n"
            "**Important:** None."
        ),
        tags=["Creator — Lessons"],
        parameters=_PATH_PARAMETERS,
        responses={
            200: OpenApiResponse(
                response=LessonSerializer,
                description="The requested lesson.",
                examples=[OpenApiExample(name="Success", value=_LESSON_EXAMPLE)],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    create=extend_schema(
        summary="Add a lesson to a module",
        description=(
            "Creates a new lesson on a module belonging to a Draft course. "
            "`id` comes back in the response so the client can immediately "
            "set the lesson's assessment without a second round-trip.\n\n"
            "Called from the 'Add lesson' action in the course builder.\n\n"
            "**Auth:** Course Creator/Writer with access to the course.\n\n"
            "**Prerequisites:** The parent course must be `DRAFT`.\n\n"
            "**Important:** `learning_objectives` is only validated for "
            "shape here (a list of non-empty strings) - the 2-5 "
            "count-per-lesson rule is enforced later, at submit time. "
            "`video_url` is optional. Returns 423 if the parent module is "
            "currently locked by another user."
        ),
        tags=["Creator — Lessons"],
        parameters=_PATH_PARAMETERS,
        request=LessonWriteSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={
                    "title": "Variables and Data Types",
                    "order": 1,
                    "script": "In this lesson we cover Python's core data types...",
                    "video_url": "https://example.com/lessons/variables.mp4",
                    "learning_objectives": ["Identify Python's built-in data types"],
                    "duration_minutes": 15,
                },
            ),
        ],
        responses={
            201: OpenApiResponse(
                response=LessonWriteSerializer,
                description="Lesson created.",
                examples=[OpenApiExample(name="Success", value=_LESSON_EXAMPLE)],
            ),
            400: _DRAFT_ONLY_400,
            423: _MODULE_LOCKED_423,
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    update=extend_schema(
        summary="Replace a lesson",
        description=(
            "Overwrites a lesson's fields. Send the full object.\n\n"
            "Called from the lesson edit form.\n\n"
            "**Auth:** Course Creator/Writer with access to the course.\n\n"
            "**Prerequisites:** The parent course must be `DRAFT`.\n\n"
            "**Important:** Returns 423 if the parent module is currently "
            "locked by another user."
        ),
        tags=["Creator — Lessons"],
        parameters=_PATH_PARAMETERS,
        request=LessonWriteSerializer,
        responses={
            200: OpenApiResponse(
                response=LessonWriteSerializer,
                description="Lesson updated.",
                examples=[OpenApiExample(name="Success", value=_LESSON_EXAMPLE)],
            ),
            400: _DRAFT_ONLY_400,
            423: _MODULE_LOCKED_423,
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    partial_update=extend_schema(
        summary="Update a lesson",
        description=(
            "Updates only the fields supplied, e.g. editing a lesson's "
            "`script` without touching anything else.\n\n"
            "Called from the lesson edit form and drag-to-reorder in the "
            "course builder.\n\n"
            "**Auth:** Course Creator/Writer with access to the course.\n\n"
            "**Prerequisites:** The parent course must be `DRAFT`.\n\n"
            "**Important:** Returns 423 if the parent module is currently "
            "locked by another user."
        ),
        tags=["Creator — Lessons"],
        parameters=_PATH_PARAMETERS,
        request=LessonWriteSerializer,
        examples=[
            OpenApiExample(
                name="Reorder",
                request_only=True,
                value={"order": 2},
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=LessonWriteSerializer,
                description="Lesson updated.",
                examples=[OpenApiExample(name="Success", value=_LESSON_EXAMPLE)],
            ),
            400: _DRAFT_ONLY_400,
            423: _MODULE_LOCKED_423,
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    destroy=extend_schema(
        summary="Delete a lesson",
        description=(
            "Deletes a lesson and cascades to its assessment. There is no "
            "undo.\n\n"
            "Called from the delete action on a lesson in the course "
            "builder.\n\n"
            "**Auth:** Course Creator/Writer with access to the course.\n\n"
            "**Prerequisites:** The parent course must be `DRAFT`.\n\n"
            "**Important:** Returns 423 if the parent module is currently "
            "locked by another user."
        ),
        tags=["Creator — Lessons"],
        parameters=_PATH_PARAMETERS,
        responses={
            204: OpenApiResponse(description="Lesson deleted."),
            400: OpenApiResponse(
                description="The parent course is not Draft.",
                examples=[
                    OpenApiExample(
                        name="Course not editable",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": (
                                        "Lessons can only be deleted while "
                                        "the course is Draft."
                                    ),
                                    "field_name": None,
                                }
                            ]
                        },
                    ),
                ],
            ),
            423: _MODULE_LOCKED_423,
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
)
class LessonViewSet(ModelViewSet):
    """Sub-resource CRUD for Lessons nested under a Draft course's module.

    Scoped at the module level (SCCS PRD Section 14): a plain COLLABORATOR
    may create/edit/delete lessons within a module they're assigned to (that
    is what "editing an assigned module" means in practice), but a module
    the caller can't access - including one they're not assigned - 404s.
    Also enforces the parent module's edit lock, same as ModuleViewSet.
    """

    permission_classes = [IsCourseCreatorRole]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Lesson.objects.none()
        accessible_modules = collaborator_service.get_modules_accessible_to(
            user=self.request.user, course_id=self.kwargs["course_pk"]
        )
        return Lesson.objects.filter(
            module_id=self.kwargs["module_pk"], module__in=accessible_modules
        )

    def get_serializer_class(self):
        if self.action in {"list", "retrieve"}:
            return LessonSerializer
        return LessonWriteSerializer

    def _get_module(self) -> Module:
        accessible_modules = collaborator_service.get_modules_accessible_to(
            user=self.request.user, course_id=self.kwargs["course_pk"]
        )
        try:
            return accessible_modules.select_related("course").get(
                pk=self.kwargs["module_pk"]
            )
        except Module.DoesNotExist as exc:
            raise exceptions.NotFound("Module not found.") from exc

    def perform_create(self, serializer):
        module = self._get_module()
        if module.course.status != CourseStatus.DRAFT:
            raise exceptions.ValidationError(
                "Lessons can only be added while the course is Draft."
            )
        module_lock_service.check_not_locked(module=module, user=self.request.user)
        lesson = serializer.save(
            module=module, created_by=self.request.user, updated_by=self.request.user
        )
        course_service.recalculate_duration_estimate(course=module.course)
        return lesson

    def perform_update(self, serializer):
        module = serializer.instance.module
        if module.course.status != CourseStatus.DRAFT:
            raise exceptions.ValidationError(
                "Lessons can only be edited while the course is Draft."
            )
        module_lock_service.check_not_locked(module=module, user=self.request.user)
        lesson = serializer.save(updated_by=self.request.user)
        course_service.recalculate_duration_estimate(course=module.course)
        return lesson

    def perform_destroy(self, instance):
        module = instance.module
        if module.course.status != CourseStatus.DRAFT:
            raise exceptions.ValidationError(
                "Lessons can only be deleted while the course is Draft."
            )
        module_lock_service.check_not_locked(module=module, user=self.request.user)
        instance.delete()
        course_service.recalculate_duration_estimate(course=module.course)

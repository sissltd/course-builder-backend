from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from django.db import transaction
from rest_framework import exceptions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from api.collaborators.services import collaborator_service
from api.courses.enums import CourseStatus
from api.courses.models import Lesson, Module
from api.courses.serializers import LessonSerializer, LessonWriteSerializer
from api.courses.serializers.ordering_serializer import ReorderSerializer
from api.courses.services import (
    course_service,
    lesson_service,
    module_lock_service,
    ordering_service,
)
from api.users.permissions import IsCourseCreatorRole
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES

_VIDEO_LESSON_REQUEST_EXAMPLE = {
    "title": "Variables and Data Types",
    "order": 1,
    "lesson_type": "VIDEO",
    "script": "In this lesson we cover Python's core data types...",
    "video_url": "https://example.com/lessons/variables.mp4",
    "embedded_link": "",
    "video_script_file": "uploads/lessons/variables.srt",
    "learning_objectives": [
        "Identify Python's built-in data types, variables, and constants"
    ],
    "duration_minutes": 15,
    "lesson_requirement": (
        "At the end of this lesson, you will understand variables and data "
        "types.\n\n1. Basic computer literacy\n2. Access to Python 3"
    ),
}

_QUIZ_LESSON_REQUEST_EXAMPLE = {
    "title": "Variables Knowledge Check",
    "order": 2,
    "lesson_type": "QUIZ",
    "script": "Check your understanding of Python variables and data types.",
    "video_url": "",
    "embedded_link": "",
    "video_script_file": "",
    "learning_objectives": ["Apply Python variable and data-type concepts"],
    "duration_minutes": 10,
    "lesson_requirement": "",
}

_TEXT_LESSON_REQUEST_EXAMPLE = {
    "title": "Python Variable Reference",
    "order": 3,
    "lesson_type": "TEXT",
    "script": "Use this lesson as a written reference for Python variables...",
    "video_url": "",
    "embedded_link": "",
    "video_script_file": "",
    "learning_objectives": ["Explain how Python variables store values"],
    "duration_minutes": 10,
    "lesson_requirement": "Basic computer literacy is recommended.",
}

_LESSON_WRITE_RESPONSE_EXAMPLE = {
    "id": "c3d4e5f6-a7b8-4c9d-0e1f-2a3b4c5d6e7f",
    **_VIDEO_LESSON_REQUEST_EXAMPLE,
    "content_type": "VIDEO",
    "requirements": [
        {
            "id": "d4e5f6a7-b8c9-4d0e-1f2a-3b4c5d6e7f80",
            "text": _VIDEO_LESSON_REQUEST_EXAMPLE["lesson_requirement"],
            "order": 1,
        }
    ],
}

_LESSON_READ_EXAMPLE = {
    **_LESSON_WRITE_RESPONSE_EXAMPLE,
    "assessment": None,
    "content_blocks": [],
    "images": [],
}

_LESSON_TYPE_REQUEST_EXAMPLES = [
    OpenApiExample(
        name="Video lesson",
        request_only=True,
        value=_VIDEO_LESSON_REQUEST_EXAMPLE,
    ),
    OpenApiExample(
        name="Quiz lesson",
        request_only=True,
        value=_QUIZ_LESSON_REQUEST_EXAMPLE,
    ),
    OpenApiExample(
        name="Text lesson",
        request_only=True,
        value=_TEXT_LESSON_REQUEST_EXAMPLE,
    ),
]

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
                examples=[OpenApiExample(name="Success", value=[_LESSON_READ_EXAMPLE])],
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
                examples=[OpenApiExample(name="Success", value=_LESSON_READ_EXAMPLE)],
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
            "**Important:** Set `lesson_type` to `VIDEO`, `QUIZ`, or `TEXT`; "
            "older clients that omit it create a `TEXT` lesson. A `VIDEO` lesson "
            "requires either `video_url` or `embedded_link`. Create a `QUIZ` "
            "lesson first, then use its returned `id` with the lesson assessment "
            "endpoint. `learning_objectives` is only validated for "
            "shape here (a list of non-empty strings) - the 2-5 "
            "count-per-lesson rule is enforced later, at submit time. Each "
            "array item is one objective; commas inside an item are preserved. "
            "`lesson_requirement` accepts the single rich-text Lesson Requirement "
            "input shown in Figma, including internal line breaks. "
            "Deprecated `content_type` remains accepted temporarily; if both type "
            "fields are sent, they must match. "
            "Returns 423 if the parent module is currently locked by another user."
        ),
        tags=["Creator — Lessons"],
        parameters=_PATH_PARAMETERS,
        request=LessonWriteSerializer,
        examples=_LESSON_TYPE_REQUEST_EXAMPLES,
        responses={
            201: OpenApiResponse(
                response=LessonWriteSerializer,
                description="Lesson created.",
                examples=[
                    OpenApiExample(name="Success", value=_LESSON_WRITE_RESPONSE_EXAMPLE)
                ],
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
            "**Important:** `lesson_type` must be `VIDEO`, `QUIZ`, or `TEXT`. "
            "A `VIDEO` lesson requires `video_url` or `embedded_link`. Returns "
            "423 if the parent module is currently locked by another user. "
            "When `lesson_requirement` is supplied it replaces the current value; "
            "omitting it preserves the value and sending an empty string clears it. "
            "Deprecated `content_type` remains accepted temporarily."
        ),
        tags=["Creator — Lessons"],
        parameters=_PATH_PARAMETERS,
        request=LessonWriteSerializer,
        examples=_LESSON_TYPE_REQUEST_EXAMPLES,
        responses={
            200: OpenApiResponse(
                response=LessonWriteSerializer,
                description="Lesson updated.",
                examples=[
                    OpenApiExample(name="Success", value=_LESSON_WRITE_RESPONSE_EXAMPLE)
                ],
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
            "**Important:** When changing `lesson_type` to `VIDEO`, also send "
            "a `video_url` or `embedded_link`. Returns 423 if the parent module "
            "is currently locked by another user. When `lesson_requirement` is "
            "supplied it replaces the current value; omitting it preserves the "
            "value and sending an empty string clears it. Deprecated `content_type` "
            "remains accepted temporarily."
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
            OpenApiExample(
                name="Change to text lesson",
                request_only=True,
                value={"lesson_type": "TEXT"},
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=LessonWriteSerializer,
                description="Lesson updated.",
                examples=[
                    OpenApiExample(name="Success", value=_LESSON_WRITE_RESPONSE_EXAMPLE)
                ],
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

    @extend_schema(
        summary="Reorder a module's lessons",
        description=(
            "Applies a new order to every lesson in the module in one "
            "request, for the builder's drag-and-drop outline.\n\n"
            "Call this once when a drag gesture settles, instead of "
            "PATCHing each lesson individually.\n\n"
            "**Auth:** Course Creator/Writer with access to the module.\n\n"
            "**Prerequisites:** The parent course must be Draft and the "
            "module must not be locked by another user.\n\n"
            "**Important:** The payload must list **every** lesson in the "
            "module, not just the ones that moved \u2014 a partial list "
            "returns 400 naming the missing ids. Order values must be "
            "distinct. The whole reorder is one transaction."
        ),
        tags=["Creator — Lessons"],
        request=ReorderSerializer,
        responses={
            200: OpenApiResponse(
                response=LessonSerializer(many=True),
                description="The module's lessons in their new order.",
            ),
            400: OpenApiResponse(
                description=(
                    "Incomplete list, unknown or duplicate ids, duplicate "
                    "order values, or the course is not Draft."
                )
            ),
            423: OpenApiResponse(description="The module is locked by another user."),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    @action(detail=False, methods=["patch"])
    def reorder(self, request, *args, **kwargs):
        module = self._get_module()
        if module.course.status != CourseStatus.DRAFT:
            raise exceptions.ValidationError(
                "Lessons can only be reordered while the course is Draft."
            )
        module_lock_service.check_not_locked(module=module, user=request.user)

        serializer = ReorderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        lessons = ordering_service.reorder(
            queryset=Lesson.objects.filter(module=module),
            items=serializer.validated_data["order"],
            actor=request.user,
        )
        return Response(LessonSerializer(lessons, many=True).data)

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

    @transaction.atomic
    def perform_create(self, serializer):
        module = self._get_module()
        if module.course.status != CourseStatus.DRAFT:
            raise exceptions.ValidationError(
                "Lessons can only be added while the course is Draft."
            )
        module_lock_service.check_not_locked(module=module, user=self.request.user)
        requirements = serializer.validated_data.pop("requirements", [])
        lesson = serializer.save(
            module=module, created_by=self.request.user, updated_by=self.request.user
        )
        lesson_service.replace_requirements(
            lesson=lesson,
            requirements=requirements,
            actor=self.request.user,
        )
        course_service.recalculate_duration_estimate(course=module.course)
        return lesson

    @transaction.atomic
    def perform_update(self, serializer):
        module = serializer.instance.module
        if module.course.status != CourseStatus.DRAFT:
            raise exceptions.ValidationError(
                "Lessons can only be edited while the course is Draft."
            )
        module_lock_service.check_not_locked(module=module, user=self.request.user)
        requirements_marker = object()
        requirements = serializer.validated_data.pop(
            "requirements", requirements_marker
        )
        lesson = serializer.save(updated_by=self.request.user)
        if requirements is not requirements_marker:
            lesson_service.replace_requirements(
                lesson=lesson,
                requirements=requirements,
                actor=self.request.user,
            )
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

from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from rest_framework import exceptions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from api.collaborators.services import collaborator_service
from api.courses.enums import CourseStatus
from api.courses.models import Course, Module
from api.courses.serializers import ModuleSerializer, ModuleWriteSerializer
from api.courses.serializers.ordering_serializer import ReorderSerializer
from api.courses.services import module_lock_service, ordering_service
from api.users.permissions import IsCourseCreatorRole
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES

_MODULE_EXAMPLE = {
    "id": "b2c3d4e5-f6a7-4b8c-9d0e-1f2a3b4c5d6e",
    "title": "Getting Started",
    "order": 1,
    "description": "Set up the tools needed to begin the course.",
    "learning_objectives": [
        "Install the required development tools",
        "Run a first program",
    ],
    "lessons": [],
    "assessment": None,
    "locked_by": None,
    "lock_expires_at": None,
    "is_locked": False,
}

_COURSE_PK_PARAMETER = OpenApiParameter(
    name="course_pk",
    type=str,
    location=OpenApiParameter.PATH,
    description="UUID of the parent course.",
)

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
                        "message": "Modules can only be edited while the course is Draft.",
                        "field_name": None,
                    }
                ]
            },
        ),
    ],
)

_MODULE_LOCKED_423 = OpenApiResponse(
    description=(
        "The module is currently locked for editing by another user "
        "(see `POST .../modules/{id}/lock/`)."
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
        summary="List a course's modules",
        description=(
            "Returns every module on the course, each with its lessons and "
            "module-level assessment nested inline. Backs the course "
            "builder's module list.\n\n"
            "Called when the course builder loads a course.\n\n"
            "**Auth:** Course Creator/Writer with access to the course "
            "(creator or collaborator).\n\n"
            "**Prerequisites:** None beyond having access to the course.\n\n"
            "**Important:** Matches ModuleViewSet's convention across the "
            "builder: `list` never 404s on a course the caller can't access "
            "- it returns an empty result set instead, since `list` doesn't "
            "call `get_object()`."
        ),
        tags=["Creator — Modules"],
        parameters=[_COURSE_PK_PARAMETER],
        responses={
            200: OpenApiResponse(
                response=ModuleSerializer(many=True),
                description="Modules, in whatever order the queryset returns them.",
                examples=[OpenApiExample(name="Success", value=[_MODULE_EXAMPLE])],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    retrieve=extend_schema(
        summary="Retrieve a module",
        description=(
            "Returns a single module with its lessons and assessment.\n\n"
            "Called when expanding a module in the course builder.\n\n"
            "**Auth:** Course Creator/Writer with access to the course.\n\n"
            "**Prerequisites:** The module must exist under the given "
            "course.\n\n"
            "**Important:** None."
        ),
        tags=["Creator — Modules"],
        parameters=[_COURSE_PK_PARAMETER],
        responses={
            200: OpenApiResponse(
                response=ModuleSerializer,
                description="The requested module.",
                examples=[OpenApiExample(name="Success", value=_MODULE_EXAMPLE)],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    create=extend_schema(
        summary="Add a module to a course",
        description=(
            "Creates a new module on a Draft course. `id` comes back in the "
            "response so the client can immediately create lessons "
            "underneath it without a second round-trip.\n\n"
            "Called from the 'Add module' action in the course builder.\n\n"
            "**Auth:** Course Creator/Writer with access to the course.\n\n"
            "**Prerequisites:** The course must be `DRAFT`.\n\n"
            "**Important:** Adding a module to a non-Draft course returns "
            "400 - the whole structural tree is frozen once a course leaves "
            "Draft."
        ),
        tags=["Creator — Modules"],
        parameters=[_COURSE_PK_PARAMETER],
        request=ModuleWriteSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request",
                request_only=True,
                value={
                    "title": "Getting Started",
                    "order": 1,
                    "description": "Set up the tools needed to begin the course.",
                    "learning_objectives": [
                        "Install the required development tools",
                        "Run a first program",
                    ],
                },
            ),
        ],
        responses={
            201: OpenApiResponse(
                response=ModuleWriteSerializer,
                description="Module created.",
                examples=[OpenApiExample(name="Success", value=_MODULE_EXAMPLE)],
            ),
            400: _DRAFT_ONLY_400,
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    update=extend_schema(
        summary="Replace a module",
        description=(
            "Overwrites a module's title, order, description, and learning "
            "objectives. Send the full object.\n\n"
            "Called from the module edit form.\n\n"
            "**Auth:** Course Creator/Writer with access to the course.\n\n"
            "**Prerequisites:** The parent course must be `DRAFT`.\n\n"
            "**Important:** Returns 423 if the module is currently locked "
            "by another user - acquire the lock first via "
            "`POST .../lock/`."
        ),
        tags=["Creator — Modules"],
        parameters=[_COURSE_PK_PARAMETER],
        request=ModuleWriteSerializer,
        responses={
            200: OpenApiResponse(
                response=ModuleWriteSerializer,
                description="Module updated.",
                examples=[OpenApiExample(name="Success", value=_MODULE_EXAMPLE)],
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
        summary="Update a module",
        description=(
            "Updates only the fields supplied, e.g. reordering modules by "
            "PATCHing `order`.\n\n"
            "Called from the module edit form and drag-to-reorder in the "
            "course builder.\n\n"
            "**Auth:** Course Creator/Writer with access to the course.\n\n"
            "**Prerequisites:** The parent course must be `DRAFT`.\n\n"
            "**Important:** Returns 423 if the module is currently locked "
            "by another user - acquire the lock first via "
            "`POST .../lock/`."
        ),
        tags=["Creator — Modules"],
        parameters=[_COURSE_PK_PARAMETER],
        request=ModuleWriteSerializer,
        examples=[
            OpenApiExample(
                name="Reorder",
                request_only=True,
                value={"order": 2},
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=ModuleWriteSerializer,
                description="Module updated.",
                examples=[OpenApiExample(name="Success", value=_MODULE_EXAMPLE)],
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
        summary="Delete a module",
        description=(
            "Deletes a module and cascades to its lessons and their "
            "assessments. There is no undo.\n\n"
            "Called from the delete action on a module in the course "
            "builder.\n\n"
            "**Auth:** Course Creator/Writer with access to the course.\n\n"
            "**Prerequisites:** The parent course must be `DRAFT`.\n\n"
            "**Important:** None."
        ),
        tags=["Creator — Modules"],
        parameters=[_COURSE_PK_PARAMETER],
        responses={
            204: OpenApiResponse(description="Module deleted."),
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
                                        "Modules can only be deleted while "
                                        "the course is Draft."
                                    ),
                                    "field_name": None,
                                }
                            ]
                        },
                    ),
                ],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
)
class ModuleViewSet(ModelViewSet):
    """Sub-resource CRUD for Modules nested under a Draft course.

    get_queryset() is filtered to the modules the requesting user can access
    (SCCS PRD Section 14: full course for the creator or an ADMIN-role
    collaborator, only explicitly assigned modules for a plain COLLABORATOR)
    so a request for a module outside that scope 404s (existence is not
    leaked via a 403) instead of relying solely on an object-level
    permission check. Structural changes (create/delete a module) go further
    and require full manage access (creator or ADMIN collaborator) - a plain
    COLLABORATOR may edit their assigned modules' content but not add/remove
    modules from the course.
    """

    permission_classes = [IsCourseCreatorRole]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Module.objects.none()
        return collaborator_service.get_modules_accessible_to(
            user=self.request.user, course_id=self.kwargs["course_pk"]
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

    def _require_manage_access(self, course: Course) -> None:
        if not collaborator_service.has_manage_access(
            course=course, user=self.request.user
        ):
            raise exceptions.PermissionDenied(
                "Only the course creator or an Admin collaborator can add or "
                "remove modules."
            )

    def perform_create(self, serializer):
        course = self._get_course()
        self._require_manage_access(course)
        if course.status != CourseStatus.DRAFT:
            raise exceptions.ValidationError(
                "Modules can only be added while the course is Draft."
            )
        serializer.save(
            course=course, created_by=self.request.user, updated_by=self.request.user
        )

    def perform_update(self, serializer):
        module = serializer.instance
        if module.course.status != CourseStatus.DRAFT:
            raise exceptions.ValidationError(
                "Modules can only be edited while the course is Draft."
            )
        module_lock_service.check_not_locked(module=module, user=self.request.user)
        serializer.save(updated_by=self.request.user)

    def perform_destroy(self, instance):
        self._require_manage_access(instance.course)
        if instance.course.status != CourseStatus.DRAFT:
            raise exceptions.ValidationError(
                "Modules can only be deleted while the course is Draft."
            )
        instance.delete()

    @extend_schema(
        summary="Reorder a course's modules",
        description=(
            "Applies a new order to every module in the course in one "
            "request, for the builder's drag-and-drop outline.\n\n"
            "Call this once when a drag gesture settles, instead of "
            "PATCHing each module individually.\n\n"
            "**Auth:** Course creator or an Admin collaborator.\n\n"
            "**Prerequisites:** The course must be Draft.\n\n"
            "**Important:** The payload must list **every** module in the "
            "course, not just the ones that moved \u2014 a partial list "
            "returns 400 naming the missing ids. Order values must be "
            "distinct. The whole reorder is one transaction: it either "
            "applies completely or not at all."
        ),
        tags=["Creator — Modules"],
        parameters=[_COURSE_PK_PARAMETER],
        request=ReorderSerializer,
        responses={
            200: OpenApiResponse(
                response=ModuleSerializer(many=True),
                description="The course's modules in their new order.",
            ),
            400: OpenApiResponse(
                description=(
                    "Incomplete list, unknown or duplicate ids, duplicate "
                    "order values, or the course is not Draft."
                )
            ),
            423: OpenApiResponse(
                description="A module in this course is locked by another user."
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    @action(detail=False, methods=["patch"])
    def reorder(self, request, *args, **kwargs):
        course = self._get_course()
        self._require_manage_access(course)
        if course.status != CourseStatus.DRAFT:
            raise exceptions.ValidationError(
                "Modules can only be reordered while the course is Draft."
            )

        serializer = ReorderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        queryset = Module.objects.filter(course=course)
        # perform_update refuses to touch a module someone else holds, so
        # the bulk path must too - otherwise reorder is a way around the
        # lock.
        for module in queryset.select_related("locked_by"):
            module_lock_service.check_not_locked(module=module, user=request.user)

        modules = ordering_service.reorder(
            queryset=queryset,
            items=serializer.validated_data["order"],
            actor=request.user,
        )
        return Response(ModuleSerializer(modules, many=True).data)

    @extend_schema(
        summary="Acquire the edit lock on a module",
        description=(
            "Acquires (or renews, if the caller already holds it) a "
            "short-TTL edit lock on the module, so two collaborators don't "
            "clobber each other's changes (SCCS PRD Section 14). This is a "
            "simple REST lock with a heartbeat, not real-time presence.\n\n"
            "**Auth:** Anyone with access to the module.\n\n"
            "**Important:** Returns 423 Locked if someone else currently "
            "holds an unexpired lock."
        ),
        tags=["Creator — Modules"],
        parameters=[_COURSE_PK_PARAMETER],
        request=None,
        responses={
            200: OpenApiResponse(response=ModuleSerializer),
            423: OpenApiResponse(description="Locked by another user."),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    @action(detail=True, methods=["post"])
    def lock(self, request, *args, **kwargs):
        module = module_lock_service.acquire_lock(
            module=self.get_object(), user=request.user
        )
        return Response(ModuleSerializer(module).data)

    @extend_schema(
        summary="Release the edit lock on a module",
        description=(
            "Releases the caller's edit lock on the module, letting someone "
            "else acquire it immediately.\n\n"
            "**Auth:** Anyone with access to the module.\n\n"
            "**Important:** No-op if the module isn't currently locked."
        ),
        tags=["Creator — Modules"],
        parameters=[_COURSE_PK_PARAMETER],
        request=None,
        responses={
            200: OpenApiResponse(response=ModuleSerializer),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    @action(detail=True, methods=["post"])
    def unlock(self, request, *args, **kwargs):
        module = module_lock_service.release_lock(
            module=self.get_object(), user=request.user
        )
        return Response(ModuleSerializer(module).data)

    @extend_schema(
        summary="Extend the edit lock on a module",
        description=(
            "Extends the caller's existing edit lock, called periodically "
            "while actively editing so the lock doesn't expire mid-edit.\n\n"
            "**Auth:** Anyone with access to the module.\n\n"
            "**Important:** Returns 423 Locked if the caller doesn't "
            "currently hold an active lock (expired, or never acquired)."
        ),
        tags=["Creator — Modules"],
        parameters=[_COURSE_PK_PARAMETER],
        request=None,
        responses={
            200: OpenApiResponse(response=ModuleSerializer),
            423: OpenApiResponse(description="No active lock held by the caller."),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    @action(detail=True, methods=["post"])
    def heartbeat(self, request, *args, **kwargs):
        module = module_lock_service.heartbeat_lock(
            module=self.get_object(), user=request.user
        )
        return Response(ModuleSerializer(module).data)

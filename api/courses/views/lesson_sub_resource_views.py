from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from rest_framework import exceptions
from rest_framework.viewsets import ModelViewSet

from api.collaborators.services import collaborator_service
from api.courses.enums import CourseStatus
from api.courses.models import (
    Lesson,
    LessonContentBlock,
    LessonImage,
    LessonRequirement,
)
from api.courses.serializers.lesson_serializer import (
    LessonContentBlockSerializer,
    LessonImageSerializer,
    LessonRequirementSerializer,
)
from api.courses.services import module_lock_service
from api.users.permissions import IsCourseCreatorRole
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES

_LESSON_PK_PARAMETER = OpenApiParameter(
    name="lesson_pk",
    type=str,
    location=OpenApiParameter.PATH,
    description="UUID of the parent lesson.",
)


class _BaseLessonSubResourceViewSet(ModelViewSet):
    """Shared plumbing for resources nested under a lesson.

    Access is resolved through the lesson's module - the same
    module-assignment rules as LessonViewSet - and every write enforces
    the parent course being Draft plus the module's edit lock, matching
    the rest of the builder's editing rules.
    """

    permission_classes = [IsCourseCreatorRole]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.model.objects.none()
        lesson = self._get_lesson(optional=self.action == "list")
        if lesson is None:
            # List convention: an inaccessible lesson's list is an empty
            # 200, not a 404 - same as ModuleViewSet/LessonViewSet.
            return self.model.objects.none()
        return self.model.objects.filter(lesson=lesson)

    def perform_create(self, serializer):
        lesson = self._get_lesson()
        self._check_editable(lesson)
        serializer.save(
            lesson=lesson, created_by=self.request.user, updated_by=self.request.user
        )

    def perform_update(self, serializer):
        self._check_editable(serializer.instance.lesson)
        serializer.save(updated_by=self.request.user)

    def perform_destroy(self, instance):
        self._check_editable(instance.lesson)
        instance.delete()

    def _get_lesson(self, optional: bool = False) -> Lesson | None:
        """Resolve the parent lesson through module-access rules.

        With `optional=True` (list actions), a lesson the caller can't
        access returns None so the caller gets an empty list rather than a
        404 - the codebase's 'list never 404s' convention. Detail and write
        actions keep the 404 so existence isn't leaked.
        """

        accessible_modules = collaborator_service.get_modules_accessible_to(
            user=self.request.user, course_id=self.kwargs["course_pk"]
        )
        try:
            return (
                Lesson.objects.filter(
                    module_id=self.kwargs["module_pk"], module__in=accessible_modules
                )
                .select_related("module", "module__course")
                .get(pk=self.kwargs["lesson_pk"])
            )
        except Lesson.DoesNotExist:
            if optional:
                return None
            raise exceptions.NotFound("Lesson not found.")

    def _check_editable(self, lesson: Lesson) -> None:
        module = lesson.module
        if module.course.status != CourseStatus.DRAFT:
            raise exceptions.ValidationError(
                f"{self.editable_noun} can only be edited while the course is Draft."
            )
        module_lock_service.check_not_locked(module=module, user=self.request.user)


@extend_schema_view(
    list=extend_schema(
        summary="List a lesson's content blocks",
        description=(
            "Returns the lesson's body blocks in order - the block-based "
            "editor's document.\n\n"
            "**Auth:** Course Creator/Writer with access to the lesson's module."
        ),
        tags=["Creator — Lessons"],
        parameters=[_LESSON_PK_PARAMETER],
        responses={
            200: OpenApiResponse(response=LessonContentBlockSerializer(many=True)),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    create=extend_schema(
        summary="Add a content block",
        description=(
            "Appends a block to the lesson body. The block's payload must "
            "match its type: prose blocks carry `text_content`, media "
            "blocks carry `media_url`, a QUIZ block references `quiz`, and "
            "a DIVIDER carries neither.\n\n"
            "**Auth:** Course Creator/Writer with access; course must be Draft "
            "and the module unlocked."
        ),
        tags=["Creator — Lessons"],
        request=LessonContentBlockSerializer,
        parameters=[_LESSON_PK_PARAMETER],
        responses={
            201: OpenApiResponse(response=LessonContentBlockSerializer),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
)
class LessonContentBlockViewSet(_BaseLessonSubResourceViewSet):
    """CRUD for a lesson's block-based body content."""

    model = LessonContentBlock
    serializer_class = LessonContentBlockSerializer
    editable_noun = "Content blocks"


@extend_schema_view(
    list=extend_schema(
        summary="List a lesson's images",
        description=(
            "Returns the images attached to a lesson via the 'Add image' "
            "modal, in display order, with captions and source metadata.\n\n"
            "**Auth:** Course Creator/Writer with access to the lesson's module."
        ),
        tags=["Creator — Lessons"],
        parameters=[_LESSON_PK_PARAMETER],
        responses={
            200: OpenApiResponse(response=LessonImageSerializer(many=True)),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    create=extend_schema(
        summary="Attach an image to a lesson",
        description=(
            "Adds an image (upload path or external URL) with optional "
            "caption to the lesson's media library.\n\n"
            "**Auth:** Course Creator/Writer with access; course must be Draft "
            "and the module unlocked."
        ),
        tags=["Creator — Lessons"],
        request=LessonImageSerializer,
        parameters=[_LESSON_PK_PARAMETER],
        responses={
            201: OpenApiResponse(response=LessonImageSerializer),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
)
class LessonImageViewSet(_BaseLessonSubResourceViewSet):
    """CRUD for a lesson's attached images."""

    model = LessonImage
    serializer_class = LessonImageSerializer
    editable_noun = "Lesson images"


@extend_schema_view(
    list=extend_schema(
        summary="List a lesson's requirements",
        description=(
            "Returns the prerequisite/requirement lines attached to a "
            "lesson, in order.\n\n"
            "**Auth:** Course Creator/Writer with access to the lesson's module."
        ),
        tags=["Creator — Lessons"],
        parameters=[_LESSON_PK_PARAMETER],
        responses={
            200: OpenApiResponse(response=LessonRequirementSerializer(many=True)),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    create=extend_schema(
        summary="Add a requirement to a lesson",
        description=(
            "Attaches one prerequisite line (e.g. 'Basic Python syntax "
            "knowledge') to the lesson.\n\n"
            "**Auth:** Course Creator/Writer with access; course must be Draft "
            "and the module unlocked."
        ),
        tags=["Creator — Lessons"],
        request=LessonRequirementSerializer,
        parameters=[_LESSON_PK_PARAMETER],
        responses={
            201: OpenApiResponse(response=LessonRequirementSerializer),
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
)
class LessonRequirementViewSet(_BaseLessonSubResourceViewSet):
    """CRUD for a lesson's requirement lines."""

    model = LessonRequirement
    serializer_class = LessonRequirementSerializer
    editable_noun = "Lesson requirements"

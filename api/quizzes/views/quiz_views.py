from django.db import IntegrityError
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from rest_framework import filters as drf_filters
from rest_framework import exceptions
from rest_framework.viewsets import ModelViewSet

from api.quizzes.filters import QuizFilter
from api.quizzes.models import Quiz
from api.quizzes.serializers import QuizSerializer
from api.quizzes.services import quiz_service
from api.users.permissions import IsAdminRole, IsCourseCreatorRole
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES

_LEVEL_PARAMETER = OpenApiParameter(
    name="level",
    type=str,
    location=OpenApiParameter.QUERY,
    description="Filter quizzes by level (LESSON, MODULE, or COURSE).",
)

_QUIZ_EXAMPLE = {
    "level": "COURSE",
    "title": "Python foundations final quiz",
    "description": "Checks the learner's understanding of the complete course.",
    "course": "02374166-a14d-4930-a462-986d5755001f",
    "passing_score": 70,
    "attempts_allowed": 2,
    "shuffle_questions": True,
    "randomize_options": True,
    "questions": [],
}


@extend_schema_view(
    list=extend_schema(
        summary="List quizzes",
        description=(
            "Returns quizzes at every level (lesson, module, course) with "
            "their questions nested. Backs the quiz picker used while "
            "building courses and the admin Quizzes screen.\n\n"
            "**Auth:** Course Creator/Writer with access to the parent course, "
            "or Admin.\n\n"
            "**Prerequisites:** None.\n\n"
            "**Important:** Filter with `?level=` to scope to one level; "
            "results are paginated."
        ),
        tags=["Creator — Quizzes"],
        parameters=[_LEVEL_PARAMETER],
        responses={
            200: OpenApiResponse(
                response=QuizSerializer(many=True),
                description="Quizzes ordered by title.",
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    retrieve=extend_schema(
        summary="Retrieve a quiz",
        description=(
            "Returns a single quiz with its questions and options nested.\n\n"
            "Use this when opening an existing quiz in the builder.\n\n"
            "**Auth:** Course Creator/Writer with access to the parent course, "
            "or Admin.\n\n"
            "**Prerequisites:** The quiz must exist in an accessible course.\n\n"
            "**Important:** A quiz outside the caller's course scope returns "
            "404, the same as an unknown id."
        ),
        tags=["Creator — Quizzes"],
        responses={
            200: OpenApiResponse(response=QuizSerializer),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    create=extend_schema(
        summary="Create a quiz",
        description=(
            "Creates a quiz attached to exactly one parent (lesson, module, "
            "or course). The `level` must match the parent field supplied; "
            "questions may be nested inline in the same request.\n\n"
            "Use this when the author first saves a quiz in the builder.\n\n"
            "**Auth:** Course Creator/Writer with access to the parent course, "
            "or Admin.\n\n"
            "**Prerequisites:** The selected lesson, module, or course must "
            "already exist and be accessible.\n\n"
            "**Important:** Nested questions and options are created atomically. "
            "Their order values must be unique within each parent, and each "
            "multiple-choice question requires exactly one correct option."
        ),
        tags=["Creator — Quizzes"],
        request=QuizSerializer,
        examples=[
            OpenApiExample(
                name="Course quiz",
                request_only=True,
                value=_QUIZ_EXAMPLE,
            )
        ],
        responses={
            201: OpenApiResponse(response=QuizSerializer),
            400: OpenApiResponse(
                description="Level/parent mismatch or invalid questions.",
                examples=[
                    OpenApiExample(
                        name="Level mismatch",
                        value={
                            "errors": [
                                {
                                    "type": "validation_error",
                                    "code": "invalid",
                                    "message": (
                                        "A LESSON-level quiz must set only the "
                                        "'lesson' field."
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
    update=extend_schema(
        summary="Replace a quiz",
        description=(
            "Overwrites a quiz's settings. Nested questions are managed via "
            "the question endpoints, not inline replacement.\n\n"
            "Use this when saving the complete quiz settings form.\n\n"
            "**Auth:** Course Creator/Writer with access to the parent course, "
            "or Admin.\n\n"
            "**Prerequisites:** The quiz and selected parent must be accessible.\n\n"
            "**Important:** Supplying `questions`, including an empty list, is "
            "rejected; manage questions through `/api/v1/questions/`."
        ),
        tags=["Creator — Quizzes"],
        request=QuizSerializer,
        examples=[
            OpenApiExample(
                name="Replacement quiz settings",
                request_only=True,
                value={
                    key: value
                    for key, value in _QUIZ_EXAMPLE.items()
                    if key != "questions"
                },
            )
        ],
        responses={
            200: OpenApiResponse(response=QuizSerializer),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    partial_update=extend_schema(
        summary="Update a quiz",
        description=(
            "Updates only the supplied fields - the normal way to tune "
            "passing score, attempts, or shuffle settings.\n\n"
            "Use this for small adjustments after initial quiz creation.\n\n"
            "**Auth:** Course Creator/Writer with access to the parent course, "
            "or Admin.\n\n"
            "**Prerequisites:** The quiz and any newly selected parent must be "
            "accessible.\n\n"
            "**Important:** Nested questions cannot be updated here."
        ),
        tags=["Creator — Quizzes"],
        request=QuizSerializer,
        examples=[
            OpenApiExample(
                name="Update passing score",
                request_only=True,
                value={"passing_score": 80},
            )
        ],
        responses={
            200: OpenApiResponse(response=QuizSerializer),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    destroy=extend_schema(
        summary="Delete a quiz",
        description=(
            "Deletes a quiz and its questions/options (cascading).\n\n"
            "Use this when removing an entire quiz from the course builder.\n\n"
            "**Auth:** Course Creator/Writer with access to the parent course, "
            "or Admin.\n\n"
            "**Prerequisites:** The quiz must exist in an accessible course.\n\n"
            "**Important:** Deletion is immediate and cascades to every question "
            "and option in the quiz."
        ),
        tags=["Creator — Quizzes"],
        responses={
            204: OpenApiResponse(description="Quiz deleted."),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
)
class QuizViewSet(ModelViewSet):
    """CRUD for relational Quizzes at lesson, module, and course level.

    Complements courses.Assessment: Assessment stores questions as a JSON
    blob managed inline by the course builder; Quiz normalizes them into
    Question/QuestionOption rows for per-option grading.
    """

    queryset = Quiz.objects.all()
    serializer_class = QuizSerializer
    permission_classes = [IsCourseCreatorRole | IsAdminRole]
    filterset_class = QuizFilter
    filter_backends = [DjangoFilterBackend, drf_filters.OrderingFilter]
    ordering_fields = ["title", "level", "created_datetime"]

    def _is_admin(self):
        return IsAdminRole().has_permission(self.request, self)

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Quiz.objects.none()
        queryset = (
            Quiz.objects.all()
            if self._is_admin()
            else quiz_service.quizzes_accessible_to(user=self.request.user)
        )
        return queryset.prefetch_related("questions", "questions__options")

    def _validate_parent_access(self, serializer):
        if self._is_admin():
            return
        instance = serializer.instance
        level = serializer.validated_data.get("level", getattr(instance, "level", None))
        parent_field = level.lower()
        parent = serializer.validated_data.get(
            parent_field, getattr(instance, parent_field, None)
        )
        if not quiz_service.user_can_access_parent(
            user=self.request.user, parent=parent
        ):
            raise exceptions.NotFound("Quiz parent not found.")

    def perform_create(self, serializer):
        self._validate_parent_access(serializer)
        try:
            serializer.save(created_by=self.request.user, updated_by=self.request.user)
        except IntegrityError as exc:
            raise exceptions.ValidationError(
                {"non_field_errors": "The quiz payload conflicts with existing data."}
            ) from exc

    def perform_update(self, serializer):
        self._validate_parent_access(serializer)
        try:
            serializer.save(updated_by=self.request.user)
        except IntegrityError as exc:
            raise exceptions.ValidationError(
                {"non_field_errors": "The quiz payload conflicts with existing data."}
            ) from exc

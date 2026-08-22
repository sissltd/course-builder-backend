from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from rest_framework import filters as drf_filters
from rest_framework.viewsets import ModelViewSet

from api.quizzes.filters import QuizFilter
from api.quizzes.models import Quiz
from api.quizzes.serializers import QuizSerializer
from api.users.permissions import IsAdminRole, IsCourseCreatorRole
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES

_LEVEL_PARAMETER = OpenApiParameter(
    name="level",
    type=str,
    location=OpenApiParameter.QUERY,
    description="Filter quizzes by level (LESSON, MODULE, or COURSE).",
)


@extend_schema_view(
    list=extend_schema(
        summary="List quizzes",
        description=(
            "Returns quizzes at every level (lesson, module, course) with "
            "their questions nested. Backs the quiz picker used while "
            "building courses and the admin Quizzes screen.\n\n"
            "**Auth:** Course Creator/Writer or Admin.\n\n"
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
            "**Auth:** Course Creator/Writer or Admin."
        ),
        tags=["Creator — Quizzes"],
        responses={
            200: OpenApiResponse(response=QuizSerializer),
            **STANDARD_ERROR_RESPONSES["auth"],
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
            "**Auth:** Course Creator/Writer or Admin."
        ),
        tags=["Creator — Quizzes"],
        request=QuizSerializer,
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
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    update=extend_schema(
        summary="Replace a quiz",
        description=(
            "Overwrites a quiz's settings. Nested questions are managed via "
            "the question endpoints, not inline replacement.\n\n"
            "**Auth:** Course Creator/Writer or Admin."
        ),
        tags=["Creator — Quizzes"],
        request=QuizSerializer,
        responses={
            200: OpenApiResponse(response=QuizSerializer),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    partial_update=extend_schema(
        summary="Update a quiz",
        description=(
            "Updates only the supplied fields - the normal way to tune "
            "passing score, attempts, or shuffle settings.\n\n"
            "**Auth:** Course Creator/Writer or Admin."
        ),
        tags=["Creator — Quizzes"],
        request=QuizSerializer,
        responses={
            200: OpenApiResponse(response=QuizSerializer),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    destroy=extend_schema(
        summary="Delete a quiz",
        description=(
            "Deletes a quiz and its questions/options (cascading).\n\n"
            "**Auth:** Course Creator/Writer or Admin."
        ),
        tags=["Creator — Quizzes"],
        responses={
            204: OpenApiResponse(description="Quiz deleted."),
            **STANDARD_ERROR_RESPONSES["auth"],
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

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Quiz.objects.none()
        return Quiz.objects.all().prefetch_related(
            "questions", "questions__options"
        )

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user, updated_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)

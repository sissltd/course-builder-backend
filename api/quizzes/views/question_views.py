from drf_spectacular.utils import (
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from rest_framework import exceptions
from rest_framework.viewsets import ModelViewSet

from api.quizzes.models import Question
from api.quizzes.serializers import QuestionSerializer
from api.users.permissions import IsAdminRole, IsCourseCreatorRole
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES


@extend_schema_view(
    list=extend_schema(
        summary="List questions",
        description=(
            "Returns questions across quizzes, ordered by their position "
            "within each quiz. Filter with `?quiz=<id>` to scope to one "
            "quiz.\n\n"
            "**Auth:** Course Creator/Writer or Admin."
        ),
        tags=["Creator — Quizzes"],
        responses={
            200: OpenApiResponse(response=QuestionSerializer(many=True)),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    retrieve=extend_schema(
        summary="Retrieve a question",
        description=(
            "Returns a single question with its options nested.\n\n"
            "**Auth:** Course Creator/Writer or Admin."
        ),
        tags=["Creator — Quizzes"],
        responses={
            200: OpenApiResponse(response=QuestionSerializer),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    create=extend_schema(
        summary="Create a question",
        description=(
            "Adds a question to a quiz. MULTIPLE_CHOICE questions require "
            "at least one nested option; ESSAY questions must not have any."
            "\n\n"
            "**Auth:** Course Creator/Writer or Admin."
        ),
        tags=["Creator — Quizzes"],
        request=QuestionSerializer,
        responses={
            201: OpenApiResponse(response=QuestionSerializer),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    update=extend_schema(
        summary="Replace a question",
        description=(
            "Overwrites a question; supplying `options` replaces the full "
            "option set.\n\n**Auth:** Course Creator/Writer or Admin."
        ),
        tags=["Creator — Quizzes"],
        request=QuestionSerializer,
        responses={
            200: OpenApiResponse(response=QuestionSerializer),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    partial_update=extend_schema(
        summary="Update a question",
        description=(
            "Updates only the supplied fields.\n\n"
            "**Auth:** Course Creator/Writer or Admin."
        ),
        tags=["Creator — Quizzes"],
        request=QuestionSerializer,
        responses={
            200: OpenApiResponse(response=QuestionSerializer),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    destroy=extend_schema(
        summary="Delete a question",
        description=(
            "Deletes a question and its options (cascading).\n\n"
            "**Auth:** Course Creator/Writer or Admin."
        ),
        tags=["Creator — Quizzes"],
        responses={
            204: OpenApiResponse(description="Question deleted."),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
)
class QuestionViewSet(ModelViewSet):
    """CRUD for Questions within a Quiz, with nested option management."""

    queryset = Question.objects.all()
    serializer_class = QuestionSerializer
    permission_classes = [IsCourseCreatorRole | IsAdminRole]
    filterset_fields = ["quiz", "question_type"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Question.objects.none()
        return Question.objects.all().prefetch_related("options")

    def perform_create(self, serializer):
        if not serializer.validated_data.get("quiz"):
            raise exceptions.ValidationError({"quiz": "This field is required."})
        serializer.save(created_by=self.request.user, updated_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)

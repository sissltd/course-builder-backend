from django.db import IntegrityError
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from rest_framework import exceptions
from rest_framework.viewsets import ModelViewSet

from api.quizzes.models import Question, Quiz
from api.quizzes.serializers import QuestionSerializer
from api.quizzes.services import quiz_service
from api.users.permissions import IsAdminRole, IsCourseCreatorRole
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES

_QUESTION_EXAMPLE = {
    "quiz": "4fc7996d-0068-42f7-8f5f-5cf82600a49f",
    "question_text": "Which keyword defines a Python function?",
    "question_type": "MULTIPLE_CHOICE",
    "points": 5,
    "model_response_guide": "",
    "order": 1,
    "options": [
        {"option_text": "func", "is_correct": False, "order": 1},
        {"option_text": "def", "is_correct": True, "order": 2},
    ],
}

_FIGMA_ASSESSMENT_NOTE = (
    "This is the relational question API for Quiz rows. The Figma Course "
    "Builder quiz editor should use the assessment endpoints instead. "
    "Assessment questions use `question`, `type`, `options[].text`, "
    "`correct_index`, `correct_indices`, `expected_answer`, and `explanation`; "
    "this endpoint uses `question_text`, `question_type`, "
    "`options[].option_text`, `is_correct`, and `model_response_guide`."
)


@extend_schema_view(
    list=extend_schema(
        summary="List questions",
        description=(
            "Returns questions across quizzes, ordered by their position "
            "within each quiz. Filter with `?quiz=<id>` to scope to one "
            "quiz.\n\n"
            "Use this endpoint to populate relational quiz question lists. "
            f"For the Figma Course Builder quiz editor, use the assessment endpoints.\n\n{_FIGMA_ASSESSMENT_NOTE}\n\n"
            "**Auth:** Course Creator/Writer with access to the parent course, "
            "or Admin.\n\n"
            "**Prerequisites:** None.\n\n"
            "**Important:** Results are paginated. `quiz` and `question_type` "
            "filters may be combined. Relational `MULTIPLE_CHOICE` means a "
            "single-correct choice question; use course assessments for Figma "
            "multi-answer questions with `correct_indices`."
        ),
        tags=["Creator — Quizzes"],
        parameters=[
            OpenApiParameter(
                name="quiz",
                type=str,
                location=OpenApiParameter.QUERY,
                description="Return questions for this quiz UUID only.",
            ),
            OpenApiParameter(
                name="question_type",
                type=str,
                location=OpenApiParameter.QUERY,
                enum=["MULTIPLE_CHOICE", "ESSAY"],
                description=(
                    "Return questions of this answer type only. This legacy "
                    "enum does not include Figma assessment `SINGLE_CHOICE`."
                ),
            ),
        ],
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
            "Use this when opening a question in the relational quiz editor. "
            f"For Figma Course Builder quizzes, use the assessment endpoints.\n\n{_FIGMA_ASSESSMENT_NOTE}\n\n"
            "**Auth:** Course Creator/Writer with access to the parent course, "
            "or Admin.\n\n"
            "**Prerequisites:** The question must exist in an accessible quiz.\n\n"
            "**Important:** Questions outside the caller's course scope return "
            "404, the same as an unknown id."
        ),
        tags=["Creator — Quizzes"],
        responses={
            200: OpenApiResponse(response=QuestionSerializer),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    create=extend_schema(
        summary="Create a question",
        description=(
            "Adds a relational question to a quiz. `MULTIPLE_CHOICE` questions "
            "require nested options; `ESSAY` questions must not have any.\n\n"
            "Call this after the parent quiz has been created.\n\n"
            f"{_FIGMA_ASSESSMENT_NOTE}\n\n"
            "**Auth:** Course Creator/Writer with access to the parent course, "
            "or Admin.\n\n"
            "**Prerequisites:** The referenced quiz must exist and be accessible.\n\n"
            "**Important:** Relational `MULTIPLE_CHOICE` requires exactly one "
            "correct option. Do not send Figma assessment fields like "
            "`type`, `question`, `correct_index`, `correct_indices`, "
            "`expected_answer`, or `options[].text` to this endpoint. "
            "Question order must be unique within the quiz, and option orders "
            "must be unique within the question."
        ),
        tags=["Creator — Quizzes"],
        request=QuestionSerializer,
        examples=[
            OpenApiExample(
                name="Multiple-choice question",
                request_only=True,
                value=_QUESTION_EXAMPLE,
            )
        ],
        responses={
            201: OpenApiResponse(response=QuestionSerializer),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    update=extend_schema(
        summary="Replace a question",
        description=(
            "Overwrites a question; supplying `options` replaces the full "
            "option set.\n\n"
            "Use this when saving the complete relational question editor "
            f"form. For Figma Course Builder quizzes, use the assessment endpoints.\n\n{_FIGMA_ASSESSMENT_NOTE}\n\n"
            "**Auth:** Course Creator/Writer with access to the parent course, "
            "or Admin.\n\n"
            "**Prerequisites:** The question and target quiz must be accessible.\n\n"
            "**Important:** The same type, correct-option, and unique-order "
            "rules as creation apply."
        ),
        tags=["Creator — Quizzes"],
        request=QuestionSerializer,
        examples=[
            OpenApiExample(
                name="Replacement question",
                request_only=True,
                value=_QUESTION_EXAMPLE,
            )
        ],
        responses={
            200: OpenApiResponse(response=QuestionSerializer),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    partial_update=extend_schema(
        summary="Update a question",
        description=(
            "Updates only the supplied fields.\n\n"
            "Use this for small relational edits such as changing text, "
            f"points, or options. For Figma Course Builder quizzes, use the assessment endpoints.\n\n{_FIGMA_ASSESSMENT_NOTE}\n\n"
            "**Auth:** Course Creator/Writer with access to the parent course, "
            "or Admin.\n\n"
            "**Prerequisites:** The question and target quiz must be accessible.\n\n"
            "**Important:** If `options` is supplied, it replaces the entire "
            "option set. Changing to ESSAY requires an empty option set."
        ),
        tags=["Creator — Quizzes"],
        request=QuestionSerializer,
        examples=[
            OpenApiExample(
                name="Update question points",
                request_only=True,
                value={"points": 10},
            )
        ],
        responses={
            200: OpenApiResponse(response=QuestionSerializer),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    ),
    destroy=extend_schema(
        summary="Delete a question",
        description=(
            "Deletes a question and its options (cascading).\n\n"
            "Use this when removing a question from a relational quiz. Figma "
            "Course Builder assessments are replaced by PUTting the desired "
            f"assessment question list.\n\n{_FIGMA_ASSESSMENT_NOTE}\n\n"
            "**Auth:** Course Creator/Writer with access to the parent course, "
            "or Admin.\n\n"
            "**Prerequisites:** The question must exist in an accessible quiz.\n\n"
            "**Important:** Deletion is immediate and also deletes every nested "
            "option."
        ),
        tags=["Creator — Quizzes"],
        responses={
            204: OpenApiResponse(description="Question deleted."),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
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

    def _accessible_quizzes(self):
        if IsAdminRole().has_permission(self.request, self):
            return Quiz.objects.all()
        return quiz_service.quizzes_accessible_to(user=self.request.user)

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Question.objects.none()
        return Question.objects.filter(
            quiz__in=self._accessible_quizzes()
        ).prefetch_related("options")

    def perform_create(self, serializer):
        quiz = serializer.validated_data.get("quiz")
        if not quiz:
            raise exceptions.ValidationError({"quiz": "This field is required."})
        if not self._accessible_quizzes().filter(pk=quiz.pk).exists():
            raise exceptions.NotFound("Quiz not found.")
        try:
            serializer.save(created_by=self.request.user, updated_by=self.request.user)
        except IntegrityError as exc:
            raise exceptions.ValidationError(
                {
                    "non_field_errors": (
                        "The question conflicts with an existing question or option."
                    )
                }
            ) from exc

    def perform_update(self, serializer):
        quiz = serializer.validated_data.get("quiz", serializer.instance.quiz)
        if not self._accessible_quizzes().filter(pk=quiz.pk).exists():
            raise exceptions.NotFound("Quiz not found.")
        try:
            serializer.save(updated_by=self.request.user)
        except IntegrityError as exc:
            raise exceptions.ValidationError(
                {
                    "non_field_errors": (
                        "The question conflicts with an existing question or option."
                    )
                }
            ) from exc

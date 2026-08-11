from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from rest_framework import exceptions
from rest_framework.response import Response
from rest_framework.views import APIView

from api.collaborators.services import collaborator_service
from api.courses.enums import AssessmentLevel, CourseStatus
from api.courses.models import Course, Lesson, Module
from api.courses.serializers import AssessmentSerializer, AssessmentWriteSerializer
from api.users.permissions import IsCourseCreatorRole
from includes.spectacular.responses import STANDARD_ERROR_RESPONSES


_MULTIPLE_CHOICE_QUESTION = {
    "type": "MULTIPLE_CHOICE",
    "question": "Which of these is a valid Python variable name?",
    "points": 10,
    "options": [
        {"text": "2var", "explanation": "Identifiers can't start with a digit."},
        {
            "text": "var_2",
            "explanation": "Correct - letters, digits, underscores are fine.",
        },
        {"text": "var-2", "explanation": "Hyphens aren't allowed in identifiers."},
    ],
    "correct_index": 1,
}

_ESSAY_QUESTION = {
    "type": "ESSAY",
    "question": "Explain the difference between a list and a tuple.",
    "points": 15,
    "explanation": (
        "Model answer guidance: lists are mutable, tuples are immutable; "
        "both are ordered sequences."
    ),
}


def _assessment_example(level: str) -> dict:
    return {
        "id": "d4e5f6a7-b8c9-4d0e-1f2a-3b4c5d6e7f8a",
        "level": level,
        "title": "Variables Quiz",
        "questions": [_MULTIPLE_CHOICE_QUESTION, _ESSAY_QUESTION],
        "summary": {
            "total_questions": 2,
            "total_points": 25,
            "multiple_choice_count": 1,
            "essay_count": 1,
        },
    }


_QUESTIONS_SAMPLE = {
    "title": "Variables Quiz",
    "questions": [_MULTIPLE_CHOICE_QUESTION, _ESSAY_QUESTION],
}

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
                        "message": "Assessments can only be edited while the course is Draft.",
                        "field_name": None,
                    }
                ]
            },
        ),
    ],
)

_ASSESSMENT_NOT_FOUND_404 = OpenApiResponse(
    description="No assessment has been set on this lesson/module/course yet.",
    examples=[
        OpenApiExample(
            name="Not found",
            value={
                "errors": [
                    {
                        "type": "client_error",
                        "code": "not_found",
                        "message": "Assessment not found.",
                        "field_name": None,
                    }
                ]
            },
        ),
    ],
)


class LessonAssessmentView(APIView):
    """GET/PUT-upsert the quiz attached to a single Lesson.

    Assessment is 1:1 per parent, so plain list/create REST semantics don't
    apply - PUT creates the assessment if absent, otherwise updates it.
    """

    permission_classes = [IsCourseCreatorRole]
    serializer_class = (
        AssessmentWriteSerializer  # for schema generation only; not a GenericAPIView
    )

    def _get_lesson(self, course_pk, module_pk, lesson_pk, user) -> Lesson:
        try:
            return Lesson.objects.select_related("module__course").get(
                pk=lesson_pk,
                module_id=module_pk,
                module__course_id=course_pk,
                module__course__in=collaborator_service.get_courses_accessible_to(user),
            )
        except Lesson.DoesNotExist as exc:
            raise exceptions.NotFound("Lesson not found.") from exc

    @extend_schema(
        summary="Retrieve a lesson's assessment",
        description=(
            "Returns the quiz attached to a single lesson.\n\n"
            "Called when opening a lesson's assessment editor in the course "
            "builder.\n\n"
            "**Auth:** Course Creator/Writer with access to the course.\n\n"
            "**Prerequisites:** The lesson must have an assessment set - "
            "use PUT to create one if it doesn't.\n\n"
            "**Important:** None."
        ),
        tags=["Quiz"],
        parameters=[
            OpenApiParameter("course_pk", str, OpenApiParameter.PATH),
            OpenApiParameter("module_pk", str, OpenApiParameter.PATH),
            OpenApiParameter("lesson_pk", str, OpenApiParameter.PATH),
        ],
        responses={
            200: OpenApiResponse(
                response=AssessmentSerializer,
                description="The lesson's assessment.",
                examples=[
                    OpenApiExample(name="Success", value=_assessment_example("LESSON"))
                ],
            ),
            404: _ASSESSMENT_NOT_FOUND_404,
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def get(self, request, course_pk, module_pk, lesson_pk):
        lesson = self._get_lesson(course_pk, module_pk, lesson_pk, request.user)
        assessment = getattr(lesson, "assessment", None)
        if not assessment:
            raise exceptions.NotFound("Assessment not found.")
        return Response(AssessmentSerializer(assessment).data)

    @extend_schema(
        summary="Create or replace a lesson's assessment",
        description=(
            "Upserts the quiz attached to a lesson: creates it if none "
            "exists yet, otherwise overwrites it.\n\n"
            "Called from the lesson assessment editor when the creator "
            "saves questions.\n\n"
            "**Auth:** Course Creator/Writer with access to the course.\n\n"
            "**Prerequisites:** The parent course must be `DRAFT`.\n\n"
            "**Important:** Each question's `type` decides which fields "
            "apply: `MULTIPLE_CHOICE` needs at least 2 `options` (each with "
            "its own `explanation`) and a `correct_index` within range; "
            "`ESSAY` needs a top-level `explanation` instead and rejects "
            "`options`/`correct_index`. Explanations are required on every "
            "option and essay question (SCCS PRD Section 6.3). Only this "
            "per-question shape is validated here - the 3-5 "
            "questions-per-lesson count threshold is enforced separately at "
            "submit time, not on every save."
        ),
        tags=["Quiz"],
        parameters=[
            OpenApiParameter("course_pk", str, OpenApiParameter.PATH),
            OpenApiParameter("module_pk", str, OpenApiParameter.PATH),
            OpenApiParameter("lesson_pk", str, OpenApiParameter.PATH),
        ],
        request=AssessmentWriteSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request", request_only=True, value=_QUESTIONS_SAMPLE
            )
        ],
        responses={
            200: OpenApiResponse(
                response=AssessmentSerializer,
                description="Assessment created or updated.",
                examples=[
                    OpenApiExample(name="Success", value=_assessment_example("LESSON"))
                ],
            ),
            400: _DRAFT_ONLY_400,
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def put(self, request, course_pk, module_pk, lesson_pk):
        lesson = self._get_lesson(course_pk, module_pk, lesson_pk, request.user)
        if lesson.module.course.status != CourseStatus.DRAFT:
            raise exceptions.ValidationError(
                "Assessments can only be edited while the course is Draft."
            )

        assessment = getattr(lesson, "assessment", None)
        serializer = AssessmentWriteSerializer(instance=assessment, data=request.data)
        serializer.is_valid(raise_exception=True)
        if assessment:
            assessment = serializer.save(updated_by=request.user)
        else:
            assessment = serializer.save(
                level=AssessmentLevel.LESSON,
                lesson=lesson,
                created_by=request.user,
                updated_by=request.user,
            )
        return Response(AssessmentSerializer(assessment).data)


class ModuleAssessmentView(APIView):
    """GET/PUT-upsert the module-level assessment attached to a single Module."""

    permission_classes = [IsCourseCreatorRole]
    serializer_class = (
        AssessmentWriteSerializer  # for schema generation only; not a GenericAPIView
    )

    def _get_module(self, course_pk, module_pk, user) -> Module:
        try:
            return Module.objects.select_related("course").get(
                pk=module_pk,
                course_id=course_pk,
                course__in=collaborator_service.get_courses_accessible_to(user),
            )
        except Module.DoesNotExist as exc:
            raise exceptions.NotFound("Module not found.") from exc

    @extend_schema(
        summary="Retrieve a module's assessment",
        description=(
            "Returns the module-level quiz attached to a single module.\n\n"
            "Called when opening a module's assessment editor in the course "
            "builder.\n\n"
            "**Auth:** Course Creator/Writer with access to the course.\n\n"
            "**Prerequisites:** The module must have an assessment set - "
            "use PUT to create one if it doesn't.\n\n"
            "**Important:** None."
        ),
        tags=["Quiz"],
        parameters=[
            OpenApiParameter("course_pk", str, OpenApiParameter.PATH),
            OpenApiParameter("module_pk", str, OpenApiParameter.PATH),
        ],
        responses={
            200: OpenApiResponse(
                response=AssessmentSerializer,
                description="The module's assessment.",
                examples=[
                    OpenApiExample(name="Success", value=_assessment_example("MODULE"))
                ],
            ),
            404: _ASSESSMENT_NOT_FOUND_404,
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def get(self, request, course_pk, module_pk):
        module = self._get_module(course_pk, module_pk, request.user)
        assessment = getattr(module, "assessment", None)
        if not assessment:
            raise exceptions.NotFound("Assessment not found.")
        return Response(AssessmentSerializer(assessment).data)

    @extend_schema(
        summary="Create or replace a module's assessment",
        description=(
            "Upserts the module-level quiz: creates it if none exists yet, "
            "otherwise overwrites it.\n\n"
            "Called from the module assessment editor when the creator "
            "saves questions.\n\n"
            "**Auth:** Course Creator/Writer with access to the course.\n\n"
            "**Prerequisites:** The parent course must be `DRAFT`.\n\n"
            "**Important:** Each question's `type` decides which fields "
            "apply: `MULTIPLE_CHOICE` needs at least 2 `options` (each with "
            "its own `explanation`) and a `correct_index` within range; "
            "`ESSAY` needs a top-level `explanation` instead and rejects "
            "`options`/`correct_index`. Explanations are required on every "
            "option and essay question (SCCS PRD Section 6.3). Only this "
            "per-question shape is validated here; count-per-level "
            "thresholds are enforced separately at submit time."
        ),
        tags=["Quiz"],
        parameters=[
            OpenApiParameter("course_pk", str, OpenApiParameter.PATH),
            OpenApiParameter("module_pk", str, OpenApiParameter.PATH),
        ],
        request=AssessmentWriteSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request", request_only=True, value=_QUESTIONS_SAMPLE
            )
        ],
        responses={
            200: OpenApiResponse(
                response=AssessmentSerializer,
                description="Assessment created or updated.",
                examples=[
                    OpenApiExample(name="Success", value=_assessment_example("MODULE"))
                ],
            ),
            400: _DRAFT_ONLY_400,
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def put(self, request, course_pk, module_pk):
        module = self._get_module(course_pk, module_pk, request.user)
        if module.course.status != CourseStatus.DRAFT:
            raise exceptions.ValidationError(
                "Assessments can only be edited while the course is Draft."
            )

        assessment = getattr(module, "assessment", None)
        serializer = AssessmentWriteSerializer(instance=assessment, data=request.data)
        serializer.is_valid(raise_exception=True)
        if assessment:
            assessment = serializer.save(updated_by=request.user)
        else:
            assessment = serializer.save(
                level=AssessmentLevel.MODULE,
                module=module,
                created_by=request.user,
                updated_by=request.user,
            )
        return Response(AssessmentSerializer(assessment).data)


class CourseAssessmentView(APIView):
    """GET/PUT-upsert the final course-level assessment attached to a Course."""

    permission_classes = [IsCourseCreatorRole]
    serializer_class = (
        AssessmentWriteSerializer  # for schema generation only; not a GenericAPIView
    )

    def _get_course(self, course_pk, user) -> Course:
        try:
            return collaborator_service.get_courses_accessible_to(user).get(
                pk=course_pk
            )
        except Course.DoesNotExist as exc:
            raise exceptions.NotFound("Course not found.") from exc

    @extend_schema(
        summary="Retrieve a course's final assessment",
        description=(
            "Returns the final, course-level quiz taken after every module "
            "is complete.\n\n"
            "Called when opening the final assessment editor in the course "
            "builder.\n\n"
            "**Auth:** Course Creator/Writer with access to the course.\n\n"
            "**Prerequisites:** The course must have a final assessment set "
            "- use PUT to create one if it doesn't.\n\n"
            "**Important:** None."
        ),
        tags=["Quiz"],
        parameters=[OpenApiParameter("course_pk", str, OpenApiParameter.PATH)],
        responses={
            200: OpenApiResponse(
                response=AssessmentSerializer,
                description="The course's final assessment.",
                examples=[
                    OpenApiExample(name="Success", value=_assessment_example("COURSE"))
                ],
            ),
            404: _ASSESSMENT_NOT_FOUND_404,
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def get(self, request, course_pk):
        course = self._get_course(course_pk, request.user)
        assessment = getattr(course, "final_assessment", None)
        if not assessment:
            raise exceptions.NotFound("Assessment not found.")
        return Response(AssessmentSerializer(assessment).data)

    @extend_schema(
        summary="Create or replace a course's final assessment",
        description=(
            "Upserts the course-level final quiz: creates it if none "
            "exists yet, otherwise overwrites it. This is one of the "
            "structural requirements checked at submit time (SCCS PRD "
            "structural standards require >=15 questions here).\n\n"
            "Called from the final assessment editor in the course "
            "builder.\n\n"
            "**Auth:** Course Creator/Writer with access to the course.\n\n"
            "**Prerequisites:** The course must be `DRAFT`.\n\n"
            "**Important:** Each question's `type` decides which fields "
            "apply: `MULTIPLE_CHOICE` needs at least 2 `options` (each with "
            "its own `explanation`) and a `correct_index` within range; "
            "`ESSAY` needs a top-level `explanation` instead and rejects "
            "`options`/`correct_index`. Explanations are required on every "
            "option and essay question (SCCS PRD Section 6.3). Only this "
            "per-question shape is validated here; the >=15 questions "
            "threshold is enforced separately at submit time, not on every "
            "save."
        ),
        tags=["Quiz"],
        parameters=[OpenApiParameter("course_pk", str, OpenApiParameter.PATH)],
        request=AssessmentWriteSerializer,
        examples=[
            OpenApiExample(
                name="Sample Request", request_only=True, value=_QUESTIONS_SAMPLE
            )
        ],
        responses={
            200: OpenApiResponse(
                response=AssessmentSerializer,
                description="Assessment created or updated.",
                examples=[
                    OpenApiExample(name="Success", value=_assessment_example("COURSE"))
                ],
            ),
            400: _DRAFT_ONLY_400,
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["permission"],
            **STANDARD_ERROR_RESPONSES["not_found"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )
    def put(self, request, course_pk):
        course = self._get_course(course_pk, request.user)
        if course.status != CourseStatus.DRAFT:
            raise exceptions.ValidationError(
                "Assessments can only be edited while the course is Draft."
            )

        assessment = getattr(course, "final_assessment", None)
        serializer = AssessmentWriteSerializer(instance=assessment, data=request.data)
        serializer.is_valid(raise_exception=True)
        if assessment:
            assessment = serializer.save(updated_by=request.user)
        else:
            assessment = serializer.save(
                level=AssessmentLevel.COURSE,
                course=course,
                created_by=request.user,
                updated_by=request.user,
            )
        return Response(AssessmentSerializer(assessment).data)

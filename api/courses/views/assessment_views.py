from rest_framework import exceptions
from rest_framework.response import Response
from rest_framework.views import APIView

from api.courses.enums import AssessmentLevel, CourseStatus
from api.courses.models import Course, Lesson, Module
from api.courses.serializers import AssessmentSerializer, AssessmentWriteSerializer
from api.users.permissions import IsCourseCreatorRole


class LessonAssessmentView(APIView):
    """GET/PUT-upsert the quiz attached to a single Lesson.

    Assessment is 1:1 per parent, so plain list/create REST semantics don't
    apply - PUT creates the assessment if absent, otherwise updates it.
    """

    permission_classes = [IsCourseCreatorRole]
    serializer_class = AssessmentWriteSerializer  # for schema generation only; not a GenericAPIView

    def _get_lesson(self, course_pk, module_pk, lesson_pk, user) -> Lesson:
        try:
            return Lesson.objects.select_related("module__course").get(
                pk=lesson_pk,
                module_id=module_pk,
                module__course_id=course_pk,
                module__course__creator=user,
            )
        except Lesson.DoesNotExist as exc:
            raise exceptions.NotFound("Lesson not found.") from exc

    def get(self, request, course_pk, module_pk, lesson_pk):
        lesson = self._get_lesson(course_pk, module_pk, lesson_pk, request.user)
        assessment = getattr(lesson, "assessment", None)
        if not assessment:
            raise exceptions.NotFound("Assessment not found.")
        return Response(AssessmentSerializer(assessment).data)

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
    serializer_class = AssessmentWriteSerializer  # for schema generation only; not a GenericAPIView

    def _get_module(self, course_pk, module_pk, user) -> Module:
        try:
            return Module.objects.select_related("course").get(
                pk=module_pk, course_id=course_pk, course__creator=user
            )
        except Module.DoesNotExist as exc:
            raise exceptions.NotFound("Module not found.") from exc

    def get(self, request, course_pk, module_pk):
        module = self._get_module(course_pk, module_pk, request.user)
        assessment = getattr(module, "assessment", None)
        if not assessment:
            raise exceptions.NotFound("Assessment not found.")
        return Response(AssessmentSerializer(assessment).data)

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
    serializer_class = AssessmentWriteSerializer  # for schema generation only; not a GenericAPIView

    def _get_course(self, course_pk, user) -> Course:
        try:
            return Course.objects.get(pk=course_pk, creator=user)
        except Course.DoesNotExist as exc:
            raise exceptions.NotFound("Course not found.") from exc

    def get(self, request, course_pk):
        course = self._get_course(course_pk, request.user)
        assessment = getattr(course, "final_assessment", None)
        if not assessment:
            raise exceptions.NotFound("Assessment not found.")
        return Response(AssessmentSerializer(assessment).data)

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

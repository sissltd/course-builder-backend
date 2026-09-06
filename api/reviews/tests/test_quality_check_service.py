from django.test import TestCase

from api.courses.models import Assessment, Lesson, Module
from api.reviews.services import quality_check_service
from api.courses.tests.factories import build_compliant_course, make_questions


class ValidateStructuralStandardsTests(TestCase):
    def test_compliant_course_passes(self):
        course = build_compliant_course()
        failures = quality_check_service.validate_structural_standards(course)
        self.assertEqual(failures, [])

    def test_fails_when_too_few_modules(self):
        course = build_compliant_course(module_count=3)
        failures = quality_check_service.validate_structural_standards(course)
        self.assertTrue(any("modules" in f for f in failures))

    def test_fails_when_too_many_modules(self):
        course = build_compliant_course(module_count=13)
        failures = quality_check_service.validate_structural_standards(course)
        self.assertTrue(any("modules" in f for f in failures))

    def test_fails_when_too_few_lessons_per_module(self):
        course = build_compliant_course(lessons_per_module=2)
        failures = quality_check_service.validate_structural_standards(course)
        self.assertTrue(any("lessons" in f for f in failures))

    def test_fails_when_course_learning_objectives_out_of_range(self):
        course = build_compliant_course()
        course.learning_objectives = ["Only one"]
        course.save()

        failures = quality_check_service.validate_structural_standards(course)
        self.assertTrue(
            any(f.startswith("Course must") and "learning objectives" in f for f in failures)
        )

    def test_fails_when_lesson_learning_objectives_out_of_range(self):
        course = build_compliant_course()
        lesson = Lesson.objects.filter(module__course=course).first()
        lesson.learning_objectives = ["Only one"]
        lesson.save()

        failures = quality_check_service.validate_structural_standards(course)
        self.assertTrue(
            any(f.startswith("Lesson") and "learning objectives" in f for f in failures)
        )

    def test_fails_when_description_too_short(self):
        course = build_compliant_course()
        course.description = "too short"
        course.save()

        failures = quality_check_service.validate_structural_standards(course)
        self.assertTrue(any("description" in f for f in failures))

    def test_fails_when_lesson_script_too_short(self):
        course = build_compliant_course()
        lesson = Lesson.objects.filter(module__course=course).first()
        lesson.script = "too short"
        lesson.save()

        failures = quality_check_service.validate_structural_standards(course)
        self.assertTrue(any("script" in f for f in failures))

    def test_fails_when_duration_out_of_range(self):
        course = build_compliant_course()
        Lesson.objects.filter(module__course=course).update(duration_minutes=1)

        failures = quality_check_service.validate_structural_standards(course)
        self.assertTrue(any("duration" in f for f in failures))

    def test_allows_lessons_without_quizzes(self):
        course = build_compliant_course()
        Assessment.objects.filter(lesson__module__course=course).delete()
        self.assertFalse(
            Assessment.objects.filter(lesson__module__course=course).exists()
        )

        failures = quality_check_service.validate_structural_standards(course)
        self.assertEqual(failures, [])

    def test_allows_any_question_count_for_optional_lesson_quizzes(self):
        course = build_compliant_course()
        lesson_assessments = list(
            Assessment.objects.filter(lesson__module__course=course)[:2]
        )
        lesson_assessments[0].questions = make_questions(1)
        lesson_assessments[0].save(update_fields=["questions"])
        lesson_assessments[1].questions = make_questions(6)
        lesson_assessments[1].save(update_fields=["questions"])

        failures = quality_check_service.validate_structural_standards(course)
        self.assertEqual(failures, [])

    def test_fails_when_module_missing_assessment(self):
        course = build_compliant_course()
        module = Module.objects.filter(course=course).first()
        Assessment.objects.filter(module=module).delete()

        failures = quality_check_service.validate_structural_standards(course)
        self.assertTrue(any("module-level assessment" in f for f in failures))

    def test_fails_when_final_assessment_missing(self):
        course = build_compliant_course()
        Assessment.objects.filter(course=course).delete()

        failures = quality_check_service.validate_structural_standards(course)
        self.assertTrue(any("final assessment" in f for f in failures))

    def test_fails_when_final_assessment_has_too_few_questions(self):
        course = build_compliant_course()
        final = Assessment.objects.get(course=course)
        final.questions = make_questions(5)
        final.save()

        failures = quality_check_service.validate_structural_standards(course)
        self.assertTrue(any("final assessment" in f for f in failures))

    def test_fails_when_preview_video_missing(self):
        course = build_compliant_course()
        course.preview_video_url = ""
        course.save()

        failures = quality_check_service.validate_structural_standards(course)
        self.assertTrue(any("preview video" in f for f in failures))

    def test_fails_when_version_is_not_selected(self):
        course = build_compliant_course()
        course.version = None
        course.save()

        failures = quality_check_service.validate_structural_standards(course)
        self.assertTrue(any("version must be selected" in f for f in failures))

    def test_fails_when_terms_not_accepted(self):
        course = build_compliant_course()
        # terms_accepted_at is NOT NULL at the DB level (it's set once at
        # creation and never cleared in practice) - mutate the in-memory
        # attribute only, without save(), to exercise this validation branch.
        course.terms_accepted_at = None

        failures = quality_check_service.validate_structural_standards(course)
        self.assertTrue(any("Terms and Conditions" in f for f in failures))


class GetCourseDurationMinutesTests(TestCase):
    def test_sums_lesson_durations_across_modules(self):
        course = build_compliant_course(module_count=4, lessons_per_module=3)
        # build_compliant_course sets every lesson to 20 minutes.
        self.assertEqual(
            quality_check_service.get_course_duration_minutes(course), 4 * 3 * 20
        )

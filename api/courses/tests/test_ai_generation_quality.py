"""AI generation targets the submission quality standards.

Generation reads the same PlatformSettings thresholds as
quality_check_service.validate_structural_standards, repairs what a JSON
schema cannot enforce (description word count, summed lesson duration),
assigns a course version, and reports the checks still failing on the job.
"""

from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase

from api.courses.ai.providers import (
    OpenAIResponsesProvider,
    build_course_outline_schema,
    build_final_assessment_schema,
)
from api.courses.enums import AIGenerationStatus
from api.courses.models import CourseVersion, Lesson
from api.courses.services import ai_generation_service
from api.courses.tasks import generate_ai_course
from api.courses.tests.factories import (
    make_category,
    make_questions,
    make_topic,
    make_user,
)
from api.platform.services import platform_settings_service
from api.reviews.services import quality_check_service

PREVIEW_VIDEO_FAILURE = "Course must have a preview video before submission (BR-015)."
COMPLIANT_DESCRIPTION = " ".join(["word"] * 150)
COMPLIANT_SCRIPT = " ".join(["word"] * 600)


def _outline(*, description=COMPLIANT_DESCRIPTION, lesson_minutes=20):
    return {
        "title": "Practical JavaScript",
        "description": description,
        "difficulty_level": "BEGINNER",
        "learning_objectives": [f"Course objective {index}." for index in range(5)],
        "tags": ["javascript", "web", "programming"],
        "planned_duration_seconds": 14400,
        "modules": [
            {
                "title": f"Module {module_index}",
                "description": "Module description",
                "learning_objectives": ["Module objective."],
                "lessons": [
                    {
                        "title": f"Lesson {module_index}-{lesson_index}",
                        "learning_objectives": ["Objective one.", "Objective two."],
                        "duration_minutes": lesson_minutes,
                    }
                    for lesson_index in range(3)
                ],
            }
            for module_index in range(4)
        ],
    }


def _module_content(*, lesson_minutes=20):
    return {
        "lessons": [
            {
                "script": COMPLIANT_SCRIPT,
                "learning_objectives": ["Objective one.", "Objective two."],
                "duration_minutes": lesson_minutes,
            }
            for _ in range(3)
        ],
        "assessment": {"title": "Module quiz", "questions": make_questions(3)},
    }


USAGE = {"input_tokens": 1, "output_tokens": 1}


class RescaledDurationsTests(SimpleTestCase):
    def test_in_range_durations_are_unchanged(self):
        self.assertEqual(
            ai_generation_service._rescaled_durations(
                [20, 30], min_total=10, max_total=100
            ),
            [20, 30],
        )

    def test_long_course_is_scaled_down_to_the_maximum(self):
        durations = ai_generation_service._rescaled_durations(
            [90] * 12, min_total=120, max_total=480
        )
        self.assertLessEqual(sum(durations), 480)
        self.assertGreaterEqual(sum(durations), 120)

    def test_short_course_is_scaled_up_to_the_minimum(self):
        durations = ai_generation_service._rescaled_durations(
            [5] * 12, min_total=120, max_total=480
        )
        self.assertGreaterEqual(sum(durations), 120)
        self.assertLessEqual(sum(durations), 480)


class AIGenerationQualityTests(TestCase):
    def setUp(self):
        self.creator = make_user()
        self.category = make_category()
        self.topic = make_topic(category=self.category)
        self.job, _ = ai_generation_service.create_course_job(
            creator=self.creator,
            validated_data={
                "course_title": "Practical JavaScript",
                "description": "Teach javascript.",
                "category": self.category,
                "topic": self.topic,
                "category_name": self.category.name,
                "topic_name": self.topic.name,
                "terms_accepted": True,
            },
        )
        publish_patcher = patch(
            "api.courses.tasks.ai_generation_service.publish_ai_generation_progress"
        )
        publish_patcher.start()
        self.addCleanup(publish_patcher.stop)

    def _provider(self, *, outline=None, module_minutes=20):
        provider = Mock(name="provider", text_model="test-model")
        provider.name = "test"
        provider.generate_course_outline.return_value = (outline or _outline(), USAGE)
        provider.generate_module_content.return_value = (
            _module_content(lesson_minutes=module_minutes),
            USAGE,
        )
        provider.generate_final_assessment.return_value = (
            {"title": "Final assessment", "questions": make_questions(15)},
            USAGE,
        )
        return provider

    def _run(self, provider):
        with patch("api.courses.tasks.get_course_ai_provider", return_value=provider):
            generate_ai_course.run(str(self.job.id))
        self.job.refresh_from_db()
        return self.job.course

    def test_compliant_generation_only_reports_the_preview_video(self):
        provider = self._provider()

        course = self._run(provider)

        self.assertEqual(self.job.status, AIGenerationStatus.COMPLETED)
        self.assertEqual(self.job.result["quality_failures"], [PREVIEW_VIDEO_FAILURE])
        self.assertEqual(
            quality_check_service.validate_structural_standards(course),
            [PREVIEW_VIDEO_FAILURE],
        )
        provider.generate_assist.assert_not_called()
        standards = provider.generate_course_outline.call_args.kwargs["standards"]
        self.assertEqual(standards, ai_generation_service.get_generation_standards())

    def test_course_gets_the_latest_active_version(self):
        latest = CourseVersion.objects.create(label="2.0", is_active=True)
        CourseVersion.objects.create(label="3.0", is_active=False)

        course = self._run(self._provider())

        self.assertEqual(course.version, latest)

    def test_short_description_is_rewritten_once(self):
        provider = self._provider(outline=_outline(description="Teach javascript."))
        rewritten = " ".join(["learn"] * 160)
        provider.generate_assist.return_value = (
            rewritten,
            {"input_tokens": 100, "output_tokens": 200},
        )

        course = self._run(provider)

        provider.generate_assist.assert_called_once()
        self.assertEqual(course.description, rewritten)
        self.assertNotIn(
            "description", " ".join(self.job.result["quality_failures"])
        )
        # outline + assist + 4 modules + final assessment
        self.assertEqual(self.job.input_tokens, 1 + 100 + 4 + 1)
        self.assertEqual(self.job.output_tokens, 1 + 200 + 4 + 1)

    def test_rewrite_still_out_of_range_keeps_original_and_is_reported(self):
        provider = self._provider(outline=_outline(description="Teach javascript."))
        provider.generate_assist.return_value = ("Still short.", USAGE)

        course = self._run(provider)

        self.assertEqual(course.description, "Teach javascript.")
        self.assertIn(
            "Course description must be between 100 and 500 words (has 2).",
            self.job.result["quality_failures"],
        )

    def test_course_duration_is_kept_within_range(self):
        # 12 lessons x 90 minutes = 1080, well over the 480-minute maximum, both
        # in the outline and again in the module content.
        provider = self._provider(
            outline=_outline(lesson_minutes=90), module_minutes=90
        )

        course = self._run(provider)

        total = sum(
            Lesson.objects.filter(module__course=course).values_list(
                "duration_minutes", flat=True
            )
        )
        self.assertGreaterEqual(total, 120)
        self.assertLessEqual(total, 480)
        course.refresh_from_db()
        self.assertEqual(course.duration_estimate_minutes, total)
        self.assertEqual(self.job.result["quality_failures"], [PREVIEW_VIDEO_FAILURE])


class ProviderStandardsTests(TestCase):
    def test_schema_and_prompt_follow_platform_settings(self):
        platform_settings_service.update_settings(
            course_learning_objectives_min=6,
            course_learning_objectives_max=7,
            course_description_word_min=150,
            course_description_word_max=300,
            course_final_assessment_min_questions=20,
        )
        standards = ai_generation_service.get_generation_standards()

        outline_schema = build_course_outline_schema(standards)
        objectives = outline_schema["properties"]["learning_objectives"]
        self.assertEqual((objectives["minItems"], objectives["maxItems"]), (6, 7))
        self.assertEqual(
            build_final_assessment_schema(standards)["properties"]["questions"][
                "minItems"
            ],
            20,
        )

        provider = OpenAIResponsesProvider()
        with patch.object(
            provider, "_structured_response", return_value=({}, {})
        ) as structured:
            provider.generate_course_outline(
                title="t",
                description="d",
                category="c",
                topic="",
                standards=standards,
            )
        prompt = structured.call_args.kwargs["prompt"]
        self.assertIn("Course description: 150-300 words", prompt)
        self.assertIn("Course learning objectives: 6-7 items", prompt)

    def test_generation_caps_structure_inside_the_allowed_range(self):
        standards = ai_generation_service.get_generation_standards()
        settings_row = platform_settings_service.get_settings()

        self.assertGreaterEqual(standards.modules_min, settings_row.course_module_count_min)
        self.assertLessEqual(standards.modules_max, settings_row.course_module_count_max)
        self.assertLessEqual(
            standards.lessons_per_module_max,
            settings_row.course_lessons_per_module_max,
        )

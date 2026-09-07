from unittest.mock import Mock, patch

from django.test import TestCase

from api.courses.enums import AIGenerationStatus
from api.courses.services import ai_generation_service
from api.courses.tasks import generate_ai_course
from api.courses.tests.factories import make_category, make_topic, make_user


def _question(number):
    return {
        "type": "MULTIPLE_CHOICE",
        "question": f"Question {number}",
        "points": 1,
        "options": [
            {"text": f"Option {index}", "explanation": "Explanation"}
            for index in range(4)
        ],
        "correct_index": 0,
    }


class AICourseGenerationTaskTests(TestCase):
    def setUp(self):
        self.creator = make_user()
        self.category = make_category()
        self.topic = make_topic(category=self.category)
        self.job, _ = ai_generation_service.create_course_job(
            creator=self.creator,
            validated_data={
                "course_title": "Practical Analytics",
                "description": "A practical analytics course.",
                "category": self.category,
                "topic": self.topic,
                "category_name": self.category.name,
                "topic_name": self.topic.name,
                "terms_accepted": True,
            },
        )

    @patch("api.courses.tasks.get_course_ai_provider")
    def test_generates_outline_then_module_details_in_smaller_requests(
        self, get_provider
    ):
        provider = Mock(name="provider", text_model="test-model")
        provider.name = "test"
        provider.generate_course_outline.return_value = (
            {
                "title": "Practical Analytics",
                "description": "Generated description",
                "difficulty_level": "BEGINNER",
                "learning_objectives": ["Analyze data", "Clean data", "Chart data"],
                "tags": ["analytics", "python", "data"],
                "planned_duration_seconds": 7200,
                "modules": [
                    {
                        "title": "Foundations",
                        "description": "Analytics foundations",
                        "learning_objectives": ["Understand analytics"],
                        "lessons": [
                            {
                                "title": "Data basics",
                                "learning_objectives": [
                                    "Define data",
                                    "Recognize data types",
                                ],
                                "duration_minutes": 30,
                            }
                        ],
                    }
                ],
            },
            {"input_tokens": 10, "output_tokens": 20},
        )
        provider.generate_module_content.return_value = (
            {
                "lessons": [
                    {
                        "script": "Detailed lesson script",
                        "learning_objectives": [
                            "Define data",
                            "Recognize data types",
                        ],
                        "duration_minutes": 30,
                    }
                ],
                "assessment": {
                    "title": "Module quiz",
                    "questions": [_question(2)],
                },
            },
            {"input_tokens": 30, "output_tokens": 40},
        )
        provider.generate_final_assessment.return_value = (
            {"title": "Final assessment", "questions": [_question(3)]},
            {"input_tokens": 50, "output_tokens": 60},
        )
        get_provider.return_value = provider

        generate_ai_course.run(str(self.job.id))

        self.job.refresh_from_db()
        self.assertEqual(self.job.status, AIGenerationStatus.COMPLETED)
        self.assertEqual(self.job.input_tokens, 90)
        self.assertEqual(self.job.output_tokens, 120)
        self.assertEqual(provider.generate_module_content.call_count, 1)
        provider.generate_final_assessment.assert_called_once()
        self.assertEqual(
            list(self.job.items.values_list("status", flat=True)),
            ["COMPLETED"] * 6,
        )
        lesson = self.job.course.modules.get().lessons.get()
        self.assertEqual(lesson.script, "Detailed lesson script")
        self.assertFalse(hasattr(lesson, "assessment"))
        self.assertTrue(hasattr(lesson.module, "assessment"))
        self.assertTrue(hasattr(self.job.course, "final_assessment"))

    @patch("api.courses.tasks.get_course_ai_provider")
    def test_provider_failure_marks_current_phase_and_job_failed(self, get_provider):
        provider = Mock(name="provider", text_model="test-model")
        provider.name = "test"
        provider.generate_course_outline.side_effect = RuntimeError("provider failed")
        get_provider.return_value = provider

        with self.assertRaises(RuntimeError):
            generate_ai_course.run(str(self.job.id))

        self.job.refresh_from_db()
        self.assertEqual(self.job.status, AIGenerationStatus.FAILED)
        self.assertEqual(self.job.stage, "Generation failed")
        self.assertEqual(self.job.items.get(key="content_objectives").status, "FAILED")

from datetime import timedelta
from unittest.mock import Mock, patch

from celery.exceptions import Retry
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from api.courses.enums import AIGenerationKind, AIGenerationStatus
from api.courses.ai.providers import AIProviderError, AIProviderRateLimited
from api.courses.models import AIGenerationJob, Assessment, Module
from api.courses.services import ai_generation_service
from api.courses.tasks import (
    _retry_provider_failure,
    generate_ai_assist,
    generate_ai_course,
    generate_ai_thumbnail,
    recover_ai_generation_jobs,
)
from api.courses.tests.factories import (
    make_category,
    make_draft_course,
    make_topic,
    make_user,
)


class AILearningObjectiveNormalizationTests(SimpleTestCase):
    def test_joins_ai_comma_fragments_back_into_one_objective(self):
        self.assertEqual(
            ai_generation_service.normalize_ai_learning_objectives(
                [
                    "Create reusable functions using different JavaScript function patterns.",
                    "Work with parameters",
                    "arguments",
                    "return values",
                    "and default parameters.",
                    "Understand variable scope",
                    "lexical scope",
                    "and closures.",
                ]
            ),
            [
                "Create reusable functions using different JavaScript function patterns.",
                "Work with parameters, arguments, return values and default parameters.",
                "Understand variable scope, lexical scope and closures.",
            ],
        )

    def test_leaves_terse_objective_lists_unchanged(self):
        self.assertEqual(
            ai_generation_service.normalize_ai_learning_objectives(
                ["Analyze data", "Clean data", "Chart data"]
            ),
            ["Analyze data", "Clean data", "Chart data"],
        )


def _question(number):
    return {
        "type": "MULTIPLE_CHOICE",
        "question": f"Question {number}",
        "points": 1,
        "options": [f"Option {index}" for index in range(4)],
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

    @patch("api.courses.tasks.ai_generation_service.publish_ai_generation_progress")
    @patch("api.courses.tasks.get_course_ai_provider")
    def test_generates_outline_then_module_details_in_smaller_requests(
        self, get_provider, publish_progress
    ):
        provider = Mock(name="provider", text_model="test-model")
        provider.name = "test"
        provider.generate_course_outline.return_value = (
            {
                "title": "Practical Analytics",
                "description": "Generated description",
                "difficulty_level": "BEGINNER",
                "learning_objectives": [
                    "Analyze data",
                    "clean data",
                    "and chart data.",
                ],
                "tags": ["analytics", "python", "data"],
                "planned_duration_seconds": 7200,
                "modules": [
                    {
                        "title": "Foundations",
                        "description": "Analytics foundations",
                        "learning_objectives": [
                            "Work with parameters",
                            "arguments",
                            "return values",
                            "and default parameters.",
                        ],
                        "lessons": [
                            {
                                "title": "Data basics",
                                "learning_objectives": [
                                    "Define data",
                                    "recognize data types",
                                    "and explain data sources.",
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
                            "recognize data types",
                            "and explain data sources.",
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
        published_payloads = [
            call.kwargs["payload"] for call in publish_progress.call_args_list
        ]
        module_done = next(
            payload for payload in published_payloads if payload["type"] == "module_done"
        )
        self.assertEqual(module_done["module_index"], 0)
        self.assertEqual(module_done["stage"], "Generated module 1.")
        completed_event = next(
            payload for payload in published_payloads if payload["type"] == "completed"
        )
        self.assertEqual(completed_event["stage"], "Course details ready")
        self.assertIn("completed", [payload["type"] for payload in published_payloads])
        lesson = self.job.course.modules.get().lessons.get()
        self.assertEqual(
            self.job.course.learning_objectives,
            ["Analyze data, clean data and chart data."],
        )
        self.assertEqual(
            lesson.module.learning_objectives,
            ["Work with parameters, arguments, return values and default parameters."],
        )
        self.assertEqual(
            lesson.learning_objectives,
            ["Define data, recognize data types and explain data sources."],
        )
        self.assertEqual(lesson.script, "Detailed lesson script")
        self.assertFalse(hasattr(lesson, "assessment"))
        self.assertTrue(hasattr(lesson.module, "assessment"))
        self.assertTrue(hasattr(self.job.course, "final_assessment"))

    @patch("api.courses.tasks.ai_generation_service.publish_ai_generation_progress")
    @patch("api.courses.tasks.get_course_ai_provider")
    def test_provider_failure_marks_current_phase_and_job_failed(
        self, get_provider, publish_progress
    ):
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
        self.assertIn(
            "failed",
            [call.kwargs["payload"]["type"] for call in publish_progress.call_args_list],
        )

    @patch("api.courses.tasks.ai_generation_service.publish_ai_generation_progress")
    @patch.object(generate_ai_course, "apply_async")
    @patch("api.courses.tasks.get_course_ai_provider")
    def test_provider_capacity_wait_reschedules_without_failing(
        self, get_provider, apply_async, publish_progress
    ):
        provider = Mock(name="provider", text_model="test-model")
        provider.name = "test"
        provider.generate_course_outline.side_effect = AIProviderRateLimited(
            retry_after_seconds=42
        )
        get_provider.return_value = provider
        apply_async.return_value.id = "rescheduled-task"

        generate_ai_course.run(str(self.job.id))

        self.job.refresh_from_db()
        self.assertEqual(self.job.status, AIGenerationStatus.QUEUED)
        self.assertEqual(self.job.stage, "Waiting for AI provider capacity...")
        self.assertIsNone(self.job.last_heartbeat_at)
        self.assertEqual(self.job.celery_task_id, "rescheduled-task")
        apply_async.assert_called_once_with(args=(str(self.job.id),), countdown=42)
        waiting_events = [
            call.kwargs["payload"]
            for call in publish_progress.call_args_list
            if call.kwargs["payload"]["type"] == "waiting"
        ]
        self.assertEqual(
            waiting_events,
            [
                {
                    "type": "waiting",
                    "stage": "Waiting for AI provider capacity...",
                    "retry_after_seconds": 42,
                }
            ],
        )

    @patch("api.courses.tasks.ai_generation_service.publish_ai_generation_progress")
    def test_retryable_provider_error_requeues_job_before_retry(self, publish_progress):
        task = Mock()
        task.request.retries = 0
        task.retry.side_effect = Retry()

        with self.assertRaises(Retry):
            _retry_provider_failure(
                task=task,
                job=self.job,
                exc=AIProviderError(retryable=True),
            )

        self.job.refresh_from_db()
        self.assertEqual(self.job.status, AIGenerationStatus.QUEUED)
        self.assertEqual(self.job.stage, "Retrying AI provider request...")
        self.assertIsNone(self.job.last_heartbeat_at)
        task.retry.assert_called_once()
        publish_progress.assert_called_once_with(
            job=self.job,
            payload={"type": "waiting", "stage": "Retrying AI provider request..."},
        )

    @patch("api.courses.tasks.get_course_ai_provider")
    def test_resumed_course_skips_materialized_work(self, get_provider):
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
                        "script": "Script",
                        "learning_objectives": ["Define data", "Recognize data types"],
                        "duration_minutes": 30,
                    }
                ],
                "assessment": {"title": "Module quiz", "questions": [_question(1)]},
            },
            {"input_tokens": 30, "output_tokens": 40},
        )
        provider.generate_final_assessment.return_value = (
            {"title": "Final assessment", "questions": [_question(2)]},
            {"input_tokens": 50, "output_tokens": 60},
        )
        get_provider.return_value = provider

        generate_ai_course.run(str(self.job.id))
        self.job.refresh_from_db()
        assessment_count = Assessment.objects.count()
        provider.reset_mock()

        self.job.status = AIGenerationStatus.RUNNING
        self.job.last_heartbeat_at = timezone.now() - timedelta(minutes=11)
        self.job.save(
            update_fields=["status", "last_heartbeat_at", "updated_datetime"]
        )
        generate_ai_course.run(str(self.job.id))

        self.job.refresh_from_db()
        self.assertEqual(self.job.status, AIGenerationStatus.COMPLETED)
        self.assertEqual(Assessment.objects.count(), assessment_count)
        provider.generate_course_outline.assert_not_called()
        provider.generate_module_content.assert_not_called()
        provider.generate_final_assessment.assert_not_called()

    @patch("api.courses.tasks.generate_ai_course.delay")
    def test_watchdog_redispatches_structure_ready_job_with_stale_heartbeat(
        self, delay
    ):
        self.job.status = AIGenerationStatus.STRUCTURE_READY
        self.job.last_heartbeat_at = timezone.now() - timedelta(minutes=11)
        self.job.dispatch_attempts = 1
        self.job.save(
            update_fields=[
                "status",
                "last_heartbeat_at",
                "dispatch_attempts",
                "updated_datetime",
            ]
        )

        result = recover_ai_generation_jobs()

        self.assertEqual(result, {"dispatched": 1})
        delay.assert_called_once_with(str(self.job.id))
        self.job.refresh_from_db()
        self.assertEqual(self.job.dispatch_attempts, 2)

    @patch("api.courses.services.ai_generation_service.publish_ai_generation_progress")
    def test_complete_job_cancellation_wins_finalization_race(self, publish_progress):
        self.job.status = AIGenerationStatus.RUNNING
        self.job.cancel_requested = True
        self.job.save(update_fields=["status", "cancel_requested", "updated_datetime"])

        completed = ai_generation_service.complete_job(
            job=self.job,
            stage="Course details ready",
            result={"course_id": "course-id", "builder_ready": True},
        )

        self.job.refresh_from_db()
        self.assertFalse(completed)
        self.assertEqual(self.job.status, AIGenerationStatus.CANCELLED)
        self.assertEqual(self.job.result, {})
        publish_progress.assert_called_once()

    @patch("api.courses.tasks.ai_generation_service.publish_ai_generation_progress")
    @patch("api.courses.tasks.get_course_ai_provider")
    def test_cancel_during_final_assessment_does_not_complete_course(
        self, get_provider, publish_progress
    ):
        course = make_draft_course(creator=self.creator, category=self.category)
        module = Module.objects.create(course=course, title="Foundations", order=1)
        Assessment.objects.create(
            level="MODULE", module=module, title="Module quiz", questions=[]
        )
        self.job.course = course
        self.job.save(update_fields=["course", "updated_datetime"])

        provider = Mock(name="provider", text_model="test-model")
        provider.name = "test"

        def cancel_during_final_assessment(*, course):
            AIGenerationJob.objects.filter(pk=self.job.pk).update(cancel_requested=True)
            return {"title": "Final assessment", "questions": []}, {
                "input_tokens": 1,
                "output_tokens": 1,
            }

        provider.generate_final_assessment.side_effect = cancel_during_final_assessment
        get_provider.return_value = provider

        generate_ai_course.run(str(self.job.id))

        self.job.refresh_from_db()
        self.assertEqual(self.job.status, AIGenerationStatus.CANCELLED)
        self.assertTrue(self.job.cancel_requested)
        self.assertFalse(Assessment.objects.filter(level="COURSE", course=course).exists())
        self.assertNotIn(
            "completed",
            [call.kwargs["payload"]["type"] for call in publish_progress.call_args_list],
        )

    @patch("api.courses.tasks.ai_generation_service.publish_ai_generation_progress")
    @patch("api.courses.tasks.get_course_ai_provider")
    def test_cancel_during_assist_provider_call_does_not_complete_job(
        self, get_provider, publish_progress
    ):
        self.job.status = AIGenerationStatus.FAILED
        self.job.save(update_fields=["status", "updated_datetime"])
        course = make_draft_course(creator=self.creator, category=self.category)
        job = ai_generation_service.create_short_job(
            creator=self.creator,
            course=course,
            kind=AIGenerationKind.ASSIST,
            request_payload={
                "field": "title",
                "current_value": "Old title",
                "instruction": "Suggest a clearer title",
            },
        )
        provider = Mock(name="provider", text_model="test-model")
        provider.name = "test"

        def cancel_during_assist(**kwargs):
            AIGenerationJob.objects.filter(pk=job.pk).update(cancel_requested=True)
            return "A better title", {"input_tokens": 1, "output_tokens": 1}

        provider.generate_assist.side_effect = cancel_during_assist
        get_provider.return_value = provider

        generate_ai_assist.run(str(job.id))

        job.refresh_from_db()
        self.assertEqual(job.status, AIGenerationStatus.CANCELLED)
        self.assertEqual(job.result, {})
        self.assertNotIn(
            "completed",
            [call.kwargs["payload"]["type"] for call in publish_progress.call_args_list],
        )

    @patch("api.courses.tasks.StorageService.upload_bytes")
    @patch("api.courses.tasks.ai_generation_service.publish_ai_generation_progress")
    @patch("api.courses.tasks.get_course_ai_provider")
    def test_cancel_during_thumbnail_provider_call_skips_upload_and_completion(
        self, get_provider, publish_progress, upload_bytes
    ):
        self.job.status = AIGenerationStatus.FAILED
        self.job.save(update_fields=["status", "updated_datetime"])
        course = make_draft_course(creator=self.creator, category=self.category)
        job = ai_generation_service.create_short_job(
            creator=self.creator,
            course=course,
            kind=AIGenerationKind.THUMBNAIL,
            request_payload={"prompt": "A data visualization cover"},
        )
        provider = Mock(name="provider", image_model="test-image-model")
        provider.name = "test"

        def cancel_during_thumbnail(*, prompt):
            AIGenerationJob.objects.filter(pk=job.pk).update(cancel_requested=True)
            return b"image-bytes"

        provider.generate_thumbnail.side_effect = cancel_during_thumbnail
        get_provider.return_value = provider

        generate_ai_thumbnail.run(str(job.id))

        job.refresh_from_db()
        self.assertEqual(job.status, AIGenerationStatus.CANCELLED)
        upload_bytes.assert_not_called()
        self.assertNotIn(
            "completed",
            [call.kwargs["payload"]["type"] for call in publish_progress.call_args_list],
        )

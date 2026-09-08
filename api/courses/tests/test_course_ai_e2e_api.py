"""End-to-end HTTP tests for the course-AI surface.

Inspired by the flow-style API tests in the codebase (e.g.
``CategoryRequestFlowTests``): every test drives real HTTP requests through
the router, serializers, permission classes, and job state machine. The
provider is mocked at the adapter boundary only, and tasks run inline
(``CELERY_TASK_ALWAYS_EAGER``), so a POST genuinely exercises view → task →
materialization → apply instead of stubbing ``.delay``.
"""

import uuid
from datetime import timedelta
from unittest.mock import patch

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from api.courses.enums import (
    AIGenerationKind,
    AIGenerationStatus,
    CourseSourceType,
)
from api.courses.models import (
    AIGenerationJob,
    Assessment,
    CourseThumbnail,
    Lesson,
    Module,
)
from api.courses.tests.factories import (
    make_category,
    make_draft_course,
    make_questions,
    make_topic,
    make_user,
)
from api.users.enums import UserRole
from shared.services.storage_service import StorageService


class FakeCourseAIProvider:
    """Deterministic stand-in for the real provider adapter."""

    name = "fake"
    text_model = "fake-text-model"
    image_model = "fake-image-model"

    def __init__(self):
        self.calls = []

    def generate_course_outline(self, **kwargs):
        self.calls.append("outline")
        return (
            {
                "title": "Practical Data Analysis",
                "description": "Generated course description",
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

    def generate_module_content(self, **kwargs):
        self.calls.append("module_content")
        return (
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
                    "questions": make_questions(3),
                },
            },
            {"input_tokens": 30, "output_tokens": 40},
        )

    def generate_final_assessment(self, **kwargs):
        self.calls.append("final_assessment")
        return (
            {"title": "Final assessment", "questions": make_questions(15)},
            {"input_tokens": 50, "output_tokens": 60},
        )

    def generate_assist(self, **kwargs):
        self.calls.append("assist")
        return ("Improved lesson title", {"input_tokens": 5, "output_tokens": 7})

    def generate_thumbnail(self, *, prompt):
        self.calls.append("thumbnail")
        return b"fake-png-bytes"


class CourseAIEndToEndTests(APITestCase):
    def setUp(self):
        self.creator = make_user()
        self.category = make_category()
        self.topic = make_topic(category=self.category)
        self.provider = FakeCourseAIProvider()
        self.client.force_authenticate(self.creator)
        # Mock the provider where the tasks resolve it, and keep the thumbnail
        # task away from real object storage.
        provider_patcher = patch(
            "api.courses.tasks.get_course_ai_provider", return_value=self.provider
        )
        provider_patcher.start()
        self.addCleanup(provider_patcher.stop)
        upload_patcher = patch.object(
            StorageService,
            "upload_bytes",
            return_value="uploads/fake/thumbnail.png",
        )
        upload_patcher.start()
        self.addCleanup(upload_patcher.stop)
        public_url_patcher = patch.object(
            StorageService,
            "public_url",
            return_value="https://cdn.test/uploads/fake/thumbnail.png",
        )
        public_url_patcher.start()
        self.addCleanup(public_url_patcher.stop)

    def _start_generation(self, **overrides):
        payload = {
            "title": "Practical Data Analysis",
            "description": "A practical course for new analysts.",
            "category": str(self.category.id),
            "topic": str(self.topic.id),
            "terms_accepted": True,
            "idempotency_key": "e2e-key-1",
        }
        payload.update(overrides)
        return self.client.post(
            "/api/v1/course-ai-generations/", payload, format="json"
        )

    def _assist_payload(self, course, *, lesson=None, **overrides):
        payload = {
            "target_type": "lesson" if lesson else "course",
            "target_id": str(lesson.id if lesson else course.id),
            "field": "title",
            "instruction": "Make it concrete.",
            "target_updated_at": timezone.now().isoformat(),
        }
        payload.update(overrides)
        return payload

    def _make_lesson(self, course, title="Lesson 1"):
        module = Module.objects.create(course=course, title="Module 1", order=1)
        return Lesson.objects.create(
            module=module, title=title, order=1, duration_minutes=10
        )

    def test_full_course_generation_completes_over_http(self):
        response = self._start_generation()

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        # The POST response reflects enqueue-time state; with eager execution
        # the DB row has already moved on, which the poll below observes.
        self.assertEqual(response.data["status"], AIGenerationStatus.QUEUED)

        job = AIGenerationJob.objects.get(pk=response.data["id"])
        self.assertEqual(job.status, AIGenerationStatus.COMPLETED)
        course = job.course
        self.assertIsNotNone(course)
        self.assertEqual(course.creator, self.creator)
        self.assertEqual(course.source_type, CourseSourceType.AI_GENERATED)
        module = course.modules.get()
        lesson = module.lessons.get()
        self.assertEqual(lesson.script, "Detailed lesson script")
        self.assertTrue(Assessment.objects.filter(module=module).exists())
        self.assertTrue(Assessment.objects.filter(course=course).exists())
        self.assertEqual(
            self.provider.calls, ["outline", "module_content", "final_assessment"]
        )
        self.assertEqual(job.input_tokens, 90)
        self.assertEqual(job.output_tokens, 120)
        self.assertEqual(job.provider, "fake")
        self.assertEqual(job.model, "fake-text-model")

        polled = self.client.get(f"/api/v1/course-ai-generations/{job.id}/")
        self.assertEqual(polled.status_code, status.HTTP_200_OK)
        self.assertEqual(polled.data["status"], AIGenerationStatus.COMPLETED)
        self.assertEqual(polled.data["result"]["course_id"], str(course.id))

        # An idempotent resubmission after completion resolves to the same job.
        replay = self._start_generation()
        self.assertEqual(replay.status_code, status.HTTP_200_OK)
        self.assertEqual(replay.data["id"], str(job.id))

    @patch("api.courses.views.ai_generation_views.generate_ai_course.delay")
    def test_no_second_ai_request_is_accepted_until_the_first_returns(self, delay):
        course = make_draft_course(creator=self.creator, category=self.category)
        AIGenerationJob.objects.create(
            creator=self.creator,
            course=course,
            kind=AIGenerationKind.ASSIST,
            status=AIGenerationStatus.RUNNING,
            request_payload={},
        )

        blocked_generation = self._start_generation(idempotency_key="e2e-key-2")
        self.assertEqual(
            blocked_generation.status_code, status.HTTP_429_TOO_MANY_REQUESTS
        )
        # The job created for the blocked attempt is rolled back: nothing may
        # linger in QUEUED and silently block every future request.
        self.assertEqual(AIGenerationJob.objects.count(), 1)
        delay.assert_not_called()

        blocked_assist = self.client.post(
            f"/api/v1/courses/{course.id}/ai-assists/",
            self._assist_payload(course),
            format="json",
        )
        self.assertEqual(blocked_assist.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

        blocked_thumbnail = self.client.post(
            f"/api/v1/courses/{course.id}/ai-thumbnail/",
            {"prompt": "A clean workspace"},
            format="json",
        )
        self.assertEqual(
            blocked_thumbnail.status_code, status.HTTP_429_TOO_MANY_REQUESTS
        )

        # Once the in-flight job reaches a terminal state, requests pass again.
        AIGenerationJob.objects.update(status=AIGenerationStatus.COMPLETED)
        unblocked = self.client.post(
            f"/api/v1/courses/{course.id}/ai-thumbnail/",
            {"prompt": "A clean workspace"},
            format="json",
        )
        self.assertEqual(unblocked.status_code, status.HTTP_202_ACCEPTED)

    @patch("api.courses.views.ai_generation_views.generate_ai_course.delay")
    def test_structure_ready_course_still_blocks_new_ai_requests(self, delay):
        course = make_draft_course(creator=self.creator, category=self.category)
        AIGenerationJob.objects.create(
            creator=self.creator,
            course=course,
            kind=AIGenerationKind.FULL_COURSE,
            status=AIGenerationStatus.STRUCTURE_READY,
            request_payload={},
        )

        response = self.client.post(
            f"/api/v1/courses/{course.id}/ai-thumbnail/",
            {"prompt": "A clean workspace"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        delay.assert_not_called()

    def test_assist_suggestion_and_apply_flow(self):
        course = make_draft_course(creator=self.creator, category=self.category)
        lesson = self._make_lesson(course)

        started = self.client.post(
            f"/api/v1/courses/{course.id}/ai-assists/",
            self._assist_payload(course, lesson=lesson),
            format="json",
        )
        self.assertEqual(started.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(started.data["kind"], AIGenerationKind.ASSIST)
        job_id = started.data["id"]
        self.assertEqual(self.provider.calls, ["assist"])

        # Assists are polled through the shared generation-job detail endpoint.
        polled = self.client.get(f"/api/v1/course-ai-generations/{job_id}/")
        self.assertEqual(polled.status_code, status.HTTP_200_OK)
        self.assertEqual(polled.data["status"], AIGenerationStatus.COMPLETED)
        self.assertEqual(
            polled.data["result"], {"suggestion": "Improved lesson title"}
        )

        applied = self.client.post(
            f"/api/v1/courses/{course.id}/ai-assists/{job_id}/apply/"
        )
        self.assertEqual(applied.status_code, status.HTTP_200_OK)
        self.assertEqual(applied.data["field"], "title")
        self.assertEqual(applied.data["value"], "Improved lesson title")
        lesson.refresh_from_db()
        self.assertEqual(lesson.title, "Improved lesson title")

    def test_assist_with_unknown_target_id_returns_404(self):
        course = make_draft_course(creator=self.creator, category=self.category)
        response = self.client.post(
            f"/api/v1/courses/{course.id}/ai-assists/",
            self._assist_payload(course, target_id=str(uuid.uuid4())),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_apply_conflicts_when_target_changed_after_generation(self):
        course = make_draft_course(creator=self.creator, category=self.category)
        lesson = self._make_lesson(course)

        started = self.client.post(
            f"/api/v1/courses/{course.id}/ai-assists/",
            self._assist_payload(
                course,
                lesson=lesson,
                target_updated_at=(timezone.now() - timedelta(minutes=5)).isoformat(),
            ),
            format="json",
        )
        self.assertEqual(started.status_code, status.HTTP_202_ACCEPTED)
        job_id = started.data["id"]

        conflict = self.client.post(
            f"/api/v1/courses/{course.id}/ai-assists/{job_id}/apply/"
        )
        self.assertEqual(conflict.status_code, status.HTTP_409_CONFLICT)
        lesson.refresh_from_db()
        self.assertEqual(lesson.title, "Lesson 1")

    def test_thumbnail_generation_and_apply_flow(self):
        course = make_draft_course(creator=self.creator, category=self.category)

        started = self.client.post(
            f"/api/v1/courses/{course.id}/ai-thumbnail/",
            {"prompt": "A clean professional workspace"},
            format="json",
        )
        self.assertEqual(started.status_code, status.HTTP_202_ACCEPTED)
        job_id = started.data["id"]
        job = AIGenerationJob.objects.get(pk=job_id)
        self.assertEqual(job.status, AIGenerationStatus.COMPLETED)
        self.assertEqual(self.provider.calls, ["thumbnail"])
        self.assertEqual(job.result["file_key"], "uploads/fake/thumbnail.png")
        self.assertEqual(job.model, "fake-image-model")

        applied = self.client.post(
            f"/api/v1/courses/{course.id}/ai-thumbnail/{job_id}/apply/"
        )
        self.assertEqual(applied.status_code, status.HTTP_200_OK)
        thumbnail = CourseThumbnail.objects.get(course=course, is_active=True)
        self.assertEqual(
            thumbnail.external_url, "https://cdn.test/uploads/fake/thumbnail.png"
        )
        course.refresh_from_db()
        self.assertEqual(
            course.thumbnail_url, "https://cdn.test/uploads/fake/thumbnail.png"
        )

        # Re-applying replaces the active thumbnail; exactly one stays active.
        reapply = self.client.post(
            f"/api/v1/courses/{course.id}/ai-thumbnail/{job_id}/apply/"
        )
        self.assertEqual(reapply.status_code, status.HTTP_200_OK)
        self.assertEqual(
            CourseThumbnail.objects.filter(course=course, is_active=True).count(), 1
        )

    def test_another_creators_job_is_not_found(self):
        response = self._start_generation()
        job_id = response.data["id"]

        self.client.force_authenticate(make_user())
        response = self.client.get(f"/api/v1/course-ai-generations/{job_id}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_non_creator_roles_are_rejected(self):
        self.client.force_authenticate(make_user(role=UserRole.CREATOR_REVIEWER))
        response = self.client.post("/api/v1/course-ai-generations/", {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_unauthenticated_requests_are_rejected(self):
        self.client.force_authenticate(user=None)
        response = self.client.post("/api/v1/course-ai-generations/", {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

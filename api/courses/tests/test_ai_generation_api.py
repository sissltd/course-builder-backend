from datetime import timedelta
from unittest.mock import patch

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from api.courses.enums import AIGenerationKind, AIGenerationStatus
from api.courses.models import AIGenerationJob
from api.courses.services import ai_generation_service
from api.courses.tests.factories import make_category, make_topic, make_user


class AIGenerationApiTests(APITestCase):
    def setUp(self):
        self.creator = make_user()
        self.category = make_category()
        self.topic = make_topic(category=self.category)
        self.client.force_authenticate(self.creator)

    @patch("api.courses.views.ai_generation_views.generate_ai_course.delay")
    def test_creator_starts_generation_with_figma_progress(self, delay):
        delay.return_value.id = "task-id"
        response = self.client.post(
            "/api/v1/course-ai-generations/",
            {
                "title": "Practical Data Analysis",
                "description": "A practical course for new analysts.",
                "category": str(self.category.id),
                "topic": str(self.topic.id),
                "terms_accepted": True,
                "idempotency_key": "create-123",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        job = AIGenerationJob.objects.get(creator=self.creator)
        self.assertEqual(job.status, AIGenerationStatus.QUEUED)
        self.assertEqual(job.celery_task_id, "task-id")
        self.assertEqual(
            list(job.items.values_list("phase", "label")),
            [
                ("CREATING_CONTENT", "Analyzing course objectives"),
                ("CREATING_CONTENT", "Generating course outlines"),
                (
                    "CREATING_CONTENT",
                    "Preparing course lessons, learning objectives etc",
                ),
                ("PREPARING_DETAILS", "Analyzing course outlines"),
                ("PREPARING_DETAILS", "Generating module information"),
                (
                    "PREPARING_DETAILS",
                    "Generating course lessons, assessments, quizzes etc",
                ),
            ],
        )
        self.assertEqual(response.data["items"][0]["phase"], "CREATING_CONTENT")
        self.assertEqual(response.data["current_phase"], "CREATING_CONTENT")
        self.assertEqual(
            job.request_payload["course_title"], "Practical Data Analysis"
        )
        delay.assert_called_once_with(str(job.id))

    @patch("api.courses.views.ai_generation_views.generate_ai_course.delay")
    def test_dispatch_failure_marks_job_failed(self, delay):
        delay.side_effect = RuntimeError("broker is unavailable")
        self.client.raise_request_exception = False

        response = self.client.post(
            "/api/v1/course-ai-generations/",
            {
                "title": "Practical Data Analysis",
                "description": "A practical course for new analysts.",
                "category": str(self.category.id),
                "terms_accepted": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        job = AIGenerationJob.objects.get(creator=self.creator)
        self.assertEqual(job.status, AIGenerationStatus.FAILED)
        self.assertEqual(job.stage, "Generation failed")
        self.assertIn("could not be queued", job.error_message)

    def test_creator_lists_in_progress_generations_for_resume(self):
        running = AIGenerationJob.objects.create(
            creator=self.creator,
            kind=AIGenerationKind.FULL_COURSE,
            status=AIGenerationStatus.RUNNING,
        )
        AIGenerationJob.objects.create(
            creator=self.creator,
            kind=AIGenerationKind.FULL_COURSE,
            status=AIGenerationStatus.COMPLETED,
        )
        AIGenerationJob.objects.create(
            creator=make_user(),
            kind=AIGenerationKind.FULL_COURSE,
            status=AIGenerationStatus.RUNNING,
        )

        response = self.client.get("/api/v1/course-ai-generations/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["status"])
        self.assertEqual(response.data["data"]["paginator"]["count"], 1)
        self.assertEqual(
            response.data["data"]["results"][0]["id"],
            str(running.id),
        )
        self.assertEqual(
            response.data["data"]["results"][0]["status"],
            AIGenerationStatus.RUNNING,
        )

    def test_creator_can_filter_generation_list_by_terminal_status(self):
        failed = AIGenerationJob.objects.create(
            creator=self.creator,
            kind=AIGenerationKind.FULL_COURSE,
            status=AIGenerationStatus.FAILED,
        )
        AIGenerationJob.objects.create(
            creator=self.creator,
            kind=AIGenerationKind.FULL_COURSE,
            status=AIGenerationStatus.RUNNING,
        )

        response = self.client.get("/api/v1/course-ai-generations/?status=FAILED")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["paginator"]["count"], 1)
        self.assertEqual(response.data["data"]["results"][0]["id"], str(failed.id))

    def test_invalid_generation_list_status_is_rejected(self):
        response = self.client.get("/api/v1/course-ai-generations/?status=STALE")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_stale_in_progress_generation_is_failed_before_list(self):
        stale = AIGenerationJob.objects.create(
            creator=self.creator,
            kind=AIGenerationKind.FULL_COURSE,
            status=AIGenerationStatus.QUEUED,
        )
        old_timestamp = timezone.now() - (
            ai_generation_service.STALE_IN_FLIGHT_AFTER + timedelta(minutes=1)
        )
        AIGenerationJob.objects.filter(pk=stale.pk).update(
            updated_datetime=old_timestamp
        )

        response = self.client.get("/api/v1/course-ai-generations/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["data"]["paginator"]["count"], 0)
        stale.refresh_from_db()
        self.assertEqual(stale.status, AIGenerationStatus.FAILED)
        self.assertIn("did not complete", stale.error_message)

    @patch("api.courses.views.ai_generation_views.generate_ai_course.delay")
    def test_legacy_course_title_alias_remains_supported(self, delay):
        delay.return_value.id = "task-id"
        response = self.client.post(
            "/api/v1/course-ai-generations/",
            {
                "course_title": "Legacy client title",
                "description": "Description",
                "category": str(self.category.id),
                "terms_accepted": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(
            AIGenerationJob.objects.get(pk=response.data["id"]).request_payload[
                "course_title"
            ],
            "Legacy client title",
        )

    @patch("api.courses.views.ai_generation_views.generate_ai_course.delay")
    def test_title_is_required(self, delay):
        response = self.client.post(
            "/api/v1/course-ai-generations/",
            {
                "description": "Description",
                "category": str(self.category.id),
                "terms_accepted": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        delay.assert_not_called()

    @patch("api.courses.views.ai_generation_views.generate_ai_course.delay")
    def test_creator_polls_both_figma_phases(self, delay):
        delay.return_value.id = "task-id"
        created = self.client.post(
            "/api/v1/course-ai-generations/",
            {
                "course_title": "Course",
                "description": "Description",
                "category": str(self.category.id),
                "terms_accepted": True,
            },
            format="json",
        )

        response = self.client.get(
            f"/api/v1/course-ai-generations/{created.data['id']}/"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            {item["phase"] for item in response.data["items"]},
            {"CREATING_CONTENT", "PREPARING_DETAILS"},
        )

    @patch("api.courses.views.ai_generation_views.generate_ai_course.delay")
    def test_idempotency_key_returns_existing_job(self, delay):
        delay.return_value.id = "task-id"
        payload = {
            "course_title": "Course",
            "description": "Description",
            "category": str(self.category.id),
            "terms_accepted": True,
            "idempotency_key": "same-key",
        }
        first = self.client.post(
            "/api/v1/course-ai-generations/", payload, format="json"
        )
        second = self.client.post(
            "/api/v1/course-ai-generations/", payload, format="json"
        )

        self.assertEqual(first.data["id"], second.data["id"])
        self.assertEqual(delay.call_count, 1)

    @patch("api.courses.views.ai_generation_views.generate_ai_course.delay")
    def test_creator_can_cancel_generation(self, delay):
        delay.return_value.id = "task-id"
        created = self.client.post(
            "/api/v1/course-ai-generations/",
            {
                "course_title": "Course",
                "description": "Description",
                "category": str(self.category.id),
                "terms_accepted": True,
            },
            format="json",
        )

        response = self.client.delete(
            f"/api/v1/course-ai-generations/{created.data['id']}/"
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertTrue(
            AIGenerationJob.objects.get(pk=created.data["id"]).cancel_requested
        )

    @patch("api.courses.views.ai_generation_views.generate_ai_course.delay")
    def test_topic_must_belong_to_category(self, delay):
        response = self.client.post(
            "/api/v1/course-ai-generations/",
            {
                "course_title": "Course",
                "description": "Description",
                "category": str(make_category().id),
                "topic": str(self.topic.id),
                "terms_accepted": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        delay.assert_not_called()

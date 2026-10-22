import json
from datetime import timedelta
from unittest.mock import Mock, patch

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from api.courses.enums import (
    AIGenerationKind,
    AIGenerationStatus,
    AIGenerationPhase,
    AIGenerationItemStatus,
)
from api.courses.models import AIGenerationJob, AIGenerationItem
from api.courses.services import ai_generation_service
from api.courses.tests.factories import make_category, make_topic, make_user


STREAM_URL = "/api/v1/course-ai-generations/{job_id}/stream/"


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
    def test_retry_resets_failed_job_and_queues_same_job(self, delay):
        job = AIGenerationJob.objects.create(
            creator=self.creator,
            kind=AIGenerationKind.FULL_COURSE,
            status=AIGenerationStatus.FAILED,
            error_message="worker unavailable",
        )
        delay.return_value.id = "retry-task-id"

        response = self.client.post(
            f"/api/v1/course-ai-generations/{job.id}/retry/", {}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        job.refresh_from_db()
        self.assertEqual(job.status, AIGenerationStatus.QUEUED)
        self.assertEqual(job.error_message, "")
        self.assertEqual(job.retry_count, 1)
        self.assertEqual(job.celery_task_id, "retry-task-id")
        delay.assert_called_once_with(str(job.id))

    def test_second_execution_claim_is_ignored(self):
        job = AIGenerationJob.objects.create(
            creator=self.creator,
            kind=AIGenerationKind.FULL_COURSE,
            status=AIGenerationStatus.QUEUED,
        )

        self.assertTrue(
            ai_generation_service.claim_job_for_execution(
                job_id=job.id, task_id="first-task", stage="Creating content..."
            )
        )
        self.assertFalse(
            ai_generation_service.claim_job_for_execution(
                job_id=job.id, task_id="duplicate-task", stage="Creating content..."
            )
        )

    def test_structure_ready_job_heartbeats_and_blocks_duplicate_claim(self):
        stale_heartbeat = timezone.now() - timedelta(minutes=5)
        job = AIGenerationJob.objects.create(
            creator=self.creator,
            kind=AIGenerationKind.FULL_COURSE,
            status=AIGenerationStatus.STRUCTURE_READY,
            last_heartbeat_at=stale_heartbeat,
        )

        ai_generation_service.heartbeat(job=job)

        job.refresh_from_db()
        self.assertGreater(job.last_heartbeat_at, stale_heartbeat)
        self.assertFalse(
            ai_generation_service.claim_job_for_execution(
                job_id=job.id, task_id="duplicate-task", stage="Creating content..."
            )
        )
        job.refresh_from_db()
        self.assertEqual(job.status, AIGenerationStatus.STRUCTURE_READY)

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

    @patch("api.courses.services.ai_generation_service.publish_ai_generation_progress")
    @patch("api.courses.views.ai_generation_views.generate_ai_course.delay")
    def test_creator_can_cancel_generation(self, delay, publish_progress):
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
        cancelled_job = AIGenerationJob.objects.get(pk=created.data["id"])
        self.assertTrue(cancelled_job.cancel_requested)
        self.assertEqual(cancelled_job.status, AIGenerationStatus.CANCELLED)
        self.assertEqual(response.data["status"], AIGenerationStatus.CANCELLED)

    @patch("api.courses.views.ai_generation_views.generate_ai_course.delay")
    def test_running_generation_cancellation_waits_for_worker_checkpoint(self, delay):
        delay.return_value.id = "task-id"
        created = self.client.post(
            "/api/v1/course-ai-generations/",
            {
                "title": "Course",
                "description": "Description",
                "category": str(self.category.id),
                "terms_accepted": True,
            },
            format="json",
        )
        job = AIGenerationJob.objects.get(pk=created.data["id"])
        job.status = AIGenerationStatus.RUNNING
        job.save(update_fields=["status", "updated_datetime"])

        response = self.client.delete(f"/api/v1/course-ai-generations/{job.id}/")

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertTrue(response.data["cancel_requested"])
        self.assertEqual(response.data["status"], AIGenerationStatus.RUNNING)
        job.refresh_from_db()
        self.assertTrue(job.cancel_requested)
        self.assertEqual(job.status, AIGenerationStatus.RUNNING)

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

    def test_stream_opens_with_snapshot_when_job_exists(self):
        job = AIGenerationJob.objects.create(
            creator=self.creator,
            kind=AIGenerationKind.FULL_COURSE,
            status=AIGenerationStatus.RUNNING,
            stage="Creating content...",
        )
        AIGenerationItem.objects.create(
            job=job,
            key="content_objectives",
            label="Analyzing course objectives",
            phase=AIGenerationPhase.CREATING_CONTENT,
            status=AIGenerationItemStatus.COMPLETED,
            order=0,
        )
        redis_client = Mock()
        pubsub = Mock()
        redis_client.pubsub.return_value = pubsub
        pubsub.get_message.side_effect = [
            None,
            {
                "type": "message",
                "data": json.dumps(
                    {
                        "job_id": str(job.id),
                        "payload": {"type": "completed"},
                    }
                ).encode(),
            },
        ]

        with patch(
            "shared.redis.redis_service.RedisService.get_redis_client",
            return_value=redis_client,
        ):
            response = self.client.get(
                STREAM_URL.format(job_id=job.id),
                HTTP_ACCEPT="text/event-stream",
            )
            body = b"".join(response.streaming_content).decode("utf-8")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response["Content-Type"].startswith("text/event-stream"))
        self.assertEqual(response["Cache-Control"], "no-cache")
        self.assertEqual(response["X-Accel-Buffering"], "no")
        self.assertIn('"event": "snapshot"', body)
        self.assertIn(str(job.id), body)
        self.assertIn(": keep-alive", body)
        redis_client.pubsub.assert_called_once_with()
        pubsub.subscribe.assert_called_once_with(
            f"user:ai-generations:{self.creator.id}"
        )
        pubsub.unsubscribe.assert_called_once_with(
            f"user:ai-generations:{self.creator.id}"
        )
        pubsub.close.assert_called_once_with()

    def test_stream_delivers_matching_redis_progress_and_closes_on_terminal_event(self):
        job = AIGenerationJob.objects.create(
            creator=self.creator,
            kind=AIGenerationKind.FULL_COURSE,
            status=AIGenerationStatus.RUNNING,
        )
        redis_client = Mock()
        pubsub = Mock()
        redis_client.pubsub.return_value = pubsub
        channel = f"user:ai-generations:{self.creator.id}"
        matching_progress = {
            "job_id": str(job.id),
            "payload": {"type": "item_running", "key": "content_objectives"},
        }
        other_job_progress = {
            "job_id": "another-job",
            "payload": {"type": "item_running", "key": "ignored"},
        }
        completed = {
            "job_id": str(job.id),
            "payload": {"type": "completed", "stage": "Course details ready"},
        }
        pubsub.get_message.side_effect = [
            {"type": "message", "data": json.dumps(other_job_progress).encode()},
            {"type": "message", "data": json.dumps(matching_progress).encode()},
            {"type": "message", "data": json.dumps(completed).encode()},
        ]

        with patch(
            "shared.redis.redis_service.RedisService.get_redis_client",
            return_value=redis_client,
        ):
            response = self.client.get(
                STREAM_URL.format(job_id=job.id),
                HTTP_ACCEPT="text/event-stream",
            )
            body = b"".join(response.streaming_content).decode("utf-8")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('"event": "snapshot"', body)
        self.assertIn('"event": "progress"', body)
        self.assertIn('"type": "item_running"', body)
        self.assertIn('"type": "completed"', body)
        self.assertNotIn("ignored", body)
        pubsub.subscribe.assert_called_once_with(channel)
        pubsub.unsubscribe.assert_called_once_with(channel)
        pubsub.close.assert_called_once_with()
        self.assertEqual(pubsub.get_message.call_count, 3)

    def test_stream_returns_not_found_for_missing_or_foreign_job(self):
        foreign_job = AIGenerationJob.objects.create(
            creator=make_user(),
            kind=AIGenerationKind.FULL_COURSE,
            status=AIGenerationStatus.RUNNING,
        )
        missing_id = "00000000-0000-0000-0000-000000000000"
        with patch(
            "shared.redis.redis_service.RedisService.get_redis_client"
        ) as get_redis_client:
            for job_id in (missing_id, str(foreign_job.id)):
                with self.subTest(job_id=job_id):
                    response = self.client.get(
                        STREAM_URL.format(job_id=job_id),
                        HTTP_ACCEPT="text/event-stream",
                    )
                    self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
                    self.assertEqual(response.data["errors"][0]["code"], "not_found")

        get_redis_client.assert_not_called()

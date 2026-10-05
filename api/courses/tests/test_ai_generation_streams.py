import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from django.test import SimpleTestCase

from api.courses.views.ai_generation_views import _async_ai_generation_event_stream


class AIGenerationASGIStreamTests(SimpleTestCase):
    def test_async_stream_delivers_redis_events_and_closes_resources(self):
        creator = SimpleNamespace(id="owner-1")
        job_id = "job-1"
        snapshot = {"id": job_id, "status": "RUNNING", "items": []}
        other_job_event = {
            "job_id": "another-job",
            "payload": {"type": "item_running", "key": "ignored"},
        }
        matching_event = {
            "job_id": job_id,
            "payload": {"type": "item_running", "key": "content_objectives"},
        }
        completed_event = {
            "job_id": job_id,
            "payload": {"type": "completed"},
        }

        pubsub = Mock()
        pubsub.subscribe = AsyncMock()
        pubsub.get_message = AsyncMock(
            side_effect=[
                {"type": "message", "data": json.dumps(other_job_event).encode()},
                {"type": "message", "data": json.dumps(matching_event).encode()},
                {"type": "message", "data": json.dumps(completed_event).encode()},
            ]
        )
        pubsub.unsubscribe = AsyncMock()
        pubsub.aclose = AsyncMock()
        redis_client = Mock()
        redis_client.pubsub.return_value = pubsub
        redis_client.aclose = AsyncMock()

        async def collect_stream():
            return [
                chunk
                async for chunk in _async_ai_generation_event_stream(
                    creator=creator,
                    job_id=job_id,
                )
            ]

        with (
            patch(
                "shared.redis.redis_service.RedisService.get_async_redis_client",
                return_value=redis_client,
            ),
            patch(
                "api.courses.views.ai_generation_views._has_owned_ai_generation_job",
                return_value=True,
            ),
            patch(
                "api.courses.views.ai_generation_views._get_ai_generation_snapshot",
                return_value=(snapshot, False),
            ),
        ):
            chunks = asyncio.run(collect_stream())

        body = "".join(chunks)
        self.assertIn('"event": "snapshot"', body)
        self.assertIn('"type": "item_running"', body)
        self.assertIn('"type": "completed"', body)
        self.assertNotIn("ignored", body)
        pubsub.subscribe.assert_awaited_once_with("user:ai-generations:owner-1")
        pubsub.unsubscribe.assert_awaited_once_with("user:ai-generations:owner-1")
        pubsub.aclose.assert_awaited_once_with()
        redis_client.aclose.assert_awaited_once_with()

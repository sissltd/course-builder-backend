import json
from unittest.mock import AsyncMock, Mock, patch

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase

from shared.redis.redis_service import RedisService


class AIGenerationProgressRedisTests(SimpleTestCase):
    @patch("shared.redis.redis_service.RedisService.get_async_redis_client")
    def test_publish_sends_job_event_to_the_owner_channel(self, get_client):
        client = Mock()
        client.publish = AsyncMock()
        client.aclose = AsyncMock()
        get_client.return_value = client
        job_id = "job-123"
        user_id = "user-456"
        progress = {"type": "item_running", "key": "content_objectives"}

        async_to_sync(RedisService.publish_ai_generation_progress)(
            job_id=job_id,
            user_id=user_id,
            payload=progress,
        )

        channel, message = client.publish.await_args.args
        self.assertEqual(channel, "user:ai-generations:user-456")
        self.assertEqual(
            json.loads(message),
            {"job_id": job_id, "payload": progress},
        )
        client.publish.assert_awaited_once()
        client.aclose.assert_awaited_once()

    @patch("shared.redis.redis_service.RedisService.get_async_redis_client")
    def test_publish_does_not_break_worker_when_redis_client_fails(self, get_client):
        get_client.side_effect = RuntimeError("Redis unavailable")

        async_to_sync(RedisService.publish_ai_generation_progress)(
            job_id="job-123",
            user_id="user-456",
            payload={"type": "completed"},
        )

        get_client.assert_called_once_with()

    @patch("shared.redis.redis_service.RedisService.get_async_redis_client")
    def test_publish_skips_when_redis_is_not_configured(self, get_client):
        get_client.return_value = None

        async_to_sync(RedisService.publish_ai_generation_progress)(
            job_id="job-123",
            user_id="user-456",
            payload={"type": "completed"},
        )

        get_client.assert_called_once_with()

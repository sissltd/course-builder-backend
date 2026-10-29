"""Tests for the provider call guard: the self-imposed minute budget and the
bounded retry of transient failures inside the OpenAI adapter. Exhausted
transient failures are then retried safely by the checkpointed Celery task.

The budget exists so our own ceiling trips before the provider's hard 429s;
the first retry tier lives at the HTTP layer because a failed request there has
produced no side effects.
"""

from unittest.mock import patch

import httpx
from django.core.cache import cache
from django.test import TestCase, override_settings

from api.courses.ai.providers import (
    AIProviderError,
    AIProviderRateLimited,
    OpenAIResponsesProvider,
)

_PROVIDER_URL = "https://api.openai.com/v1/responses"


def _provider_response(status_code=200, json_data=None, headers=None):
    return httpx.Response(
        status_code,
        headers=headers or {},
        json=json_data if json_data is not None else {"output_text": "ok"},
        request=httpx.Request("POST", _PROVIDER_URL),
    )


class ProviderCallGuardTests(TestCase):
    def setUp(self):
        cache.clear()
        self.provider = OpenAIResponsesProvider()

    @override_settings(COURSE_AI_CALLS_PER_MINUTE=2)
    def test_calls_beyond_the_minute_budget_are_refused_locally(self):
        with (
            patch("api.courses.ai.providers.httpx.post") as post,
            patch("api.courses.ai.providers.time.sleep"),
        ):
            post.return_value = _provider_response()

            self.provider._post("responses", {})
            self.provider._post("responses", {})
            with self.assertRaises(AIProviderRateLimited):
                self.provider._post("responses", {})

        # The refused call never reached the provider.
        self.assertEqual(post.call_count, 2)

    def test_transient_provider_failures_are_retried(self):
        with (
            patch("api.courses.ai.providers.httpx.post") as post,
            patch("api.courses.ai.providers.time.sleep") as sleep,
        ):
            post.side_effect = [
                _provider_response(500),
                _provider_response(503),
                _provider_response(),
            ]
            data = self.provider._post("responses", {})

        self.assertEqual(data, {"output_text": "ok"})
        self.assertEqual(post.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_retry_after_header_is_honored(self):
        with (
            patch("api.courses.ai.providers.httpx.post") as post,
            patch("api.courses.ai.providers.time.sleep") as sleep,
        ):
            post.side_effect = [
                _provider_response(429, headers={"Retry-After": "7"}),
                _provider_response(),
            ]
            self.provider._post("responses", {})

        sleep.assert_called_once_with(7)

    def test_non_retryable_client_error_fails_without_retry(self):
        with (
            patch("api.courses.ai.providers.httpx.post") as post,
            patch("api.courses.ai.providers.time.sleep") as sleep,
        ):
            post.return_value = _provider_response(400)
            with self.assertRaises(AIProviderError):
                self.provider._post("responses", {})

        self.assertEqual(post.call_count, 1)
        sleep.assert_not_called()

    def test_transport_errors_are_retried_then_surfaced(self):
        with (
            patch(
                "api.courses.ai.providers.httpx.post", side_effect=httpx.ConnectError("down")
            ) as post,
            patch("api.courses.ai.providers.time.sleep"),
        ):
            with self.assertRaises(AIProviderError):
                self.provider._post("responses", {})

        self.assertEqual(post.call_count, 3)

    def test_exhausted_retries_surface_as_provider_error(self):
        with (
            patch("api.courses.ai.providers.httpx.post") as post,
            patch("api.courses.ai.providers.time.sleep"),
        ):
            post.return_value = _provider_response(500)
            with self.assertRaises(AIProviderError):
                self.provider._post("responses", {})

        self.assertEqual(post.call_count, 3)

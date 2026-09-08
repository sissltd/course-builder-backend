import base64
import math
import time
from abc import ABC, abstractmethod

import httpx
from django.conf import settings
from django.core.cache import cache
from rest_framework.exceptions import APIException


class AIProviderError(APIException):
    status_code = 502
    default_detail = "The AI provider could not complete this request."

    def __init__(self, *, retryable=False, detail=None):
        self.retryable = retryable
        super().__init__(detail=detail)


class AIProviderRateLimited(AIProviderError):
    """Raised when a call is refused by our own budget, not by the provider."""

    status_code = 429
    default_detail = "The AI service is busy right now. Please try again shortly."

    def __init__(self, *, retry_after_seconds):
        self.retry_after_seconds = retry_after_seconds
        super().__init__()


# Provider calls are metered over a rolling minute window and refused locally
# once the budget is spent - the point is to hit our ceiling *before* the
# provider's hard 429s (and the billing surprises that follow them).
PROVIDER_RATE_WINDOW_SECONDS = 60
PROVIDER_HTTP_TIMEOUT_SECONDS = 180
PROVIDER_RETRY_ATTEMPTS = 3
PROVIDER_RETRY_DELAYS_SECONDS = (2, 6)
# Statuses worth a second attempt: request timeouts, early hints, throttling,
# and gateway/provider 5xx. Everything else (4xx especially) is deterministic
# and fails immediately.
RETRYABLE_HTTP_STATUSES = {408, 425, 429, 500, 502, 503, 504}


QUESTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["type", "question", "points", "options", "correct_index"],
    "properties": {
        "type": {"type": "string", "const": "MULTIPLE_CHOICE"},
        "question": {"type": "string"},
        "points": {"type": "integer", "minimum": 1},
        "options": {
            "type": "array",
            "minItems": 4,
            "maxItems": 4,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text", "explanation"],
                "properties": {
                    "text": {"type": "string"},
                    "explanation": {"type": "string"},
                },
            },
        },
        "correct_index": {"type": "integer", "minimum": 0, "maximum": 3},
    },
}

ASSESSMENT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "questions"],
    "properties": {
        "title": {"type": "string"},
        "questions": {
            "type": "array",
            "minItems": 3,
            "items": QUESTION_SCHEMA,
        },
    },
}

COURSE_OUTLINE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "title",
        "description",
        "difficulty_level",
        "learning_objectives",
        "tags",
        "planned_duration_seconds",
        "modules",
    ],
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "difficulty_level": {
            "type": "string",
            "enum": ["BEGINNER", "INTERMEDIATE", "ADVANCED"],
        },
        "learning_objectives": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 3,
            "maxItems": 8,
        },
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 3,
            "maxItems": 10,
        },
        "planned_duration_seconds": {
            "type": "integer",
            "minimum": 7200,
            "maximum": 28800,
        },
        "modules": {
            "type": "array",
            "minItems": 5,
            "maxItems": 8,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "title",
                    "description",
                    "learning_objectives",
                    "lessons",
                ],
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "learning_objectives": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "lessons": {
                        "type": "array",
                        "minItems": 3,
                        "maxItems": 5,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [
                                "title",
                                "learning_objectives",
                                "duration_minutes",
                            ],
                            "properties": {
                                "title": {"type": "string"},
                                "learning_objectives": {
                                    "type": "array",
                                    "minItems": 2,
                                    "maxItems": 5,
                                    "items": {"type": "string"},
                                },
                                "duration_minutes": {
                                    "type": "integer",
                                    "minimum": 5,
                                    "maximum": 90,
                                },
                            },
                        },
                    },
                },
            },
        },
    },
}

MODULE_CONTENT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["lessons", "assessment"],
    "properties": {
        "lessons": {
            "type": "array",
            "minItems": 3,
            "maxItems": 5,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "script",
                    "learning_objectives",
                    "duration_minutes",
                ],
                "properties": {
                    "script": {"type": "string"},
                    "learning_objectives": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 5,
                        "items": {"type": "string"},
                    },
                    "duration_minutes": {
                        "type": "integer",
                        "minimum": 5,
                        "maximum": 90,
                    },
                },
            },
        },
        "assessment": ASSESSMENT_SCHEMA,
    },
}

FINAL_ASSESSMENT_SCHEMA = {
    **ASSESSMENT_SCHEMA,
    "properties": {
        **ASSESSMENT_SCHEMA["properties"],
        "questions": {
            **ASSESSMENT_SCHEMA["properties"]["questions"],
            "minItems": 15,
        },
    },
}


def _enforce_provider_rate_window():
    """Count one outgoing provider call, refusing it once the minute is spent."""

    limit = settings.COURSE_AI_CALLS_PER_MINUTE
    if limit <= 0:
        return
    now = time.time()
    window = int(now // PROVIDER_RATE_WINDOW_SECONDS)
    key = f"course-ai:provider-calls:{window}"
    try:
        cache.add(key, 0, timeout=PROVIDER_RATE_WINDOW_SECONDS * 2)
        if cache.incr(key) > limit:
            window_ends_at = (window + 1) * PROVIDER_RATE_WINDOW_SECONDS
            raise AIProviderRateLimited(
                retry_after_seconds=max(1, math.ceil(window_ends_at - now))
            )
    except AIProviderRateLimited:
        raise
    except Exception:  # noqa: BLE001 - a limiter outage must not block generation
        # Fail open: rate limiting is a protective budget, not correctness.
        return


def _retry_delay_seconds(attempt, response):
    """Honor Retry-After when the provider sends one, else back off briefly."""

    if response is not None:
        retry_after = response.headers.get("Retry-After", "")
        if retry_after.isdigit():
            return min(60, int(retry_after))
    delays = PROVIDER_RETRY_DELAYS_SECONDS
    return delays[min(attempt, len(delays) - 1)]


class CourseAIProvider(ABC):
    name = "unknown"

    @abstractmethod
    def generate_course_outline(self, *, title, description, category, topic): ...

    @abstractmethod
    def generate_module_content(self, *, course, module): ...

    @abstractmethod
    def generate_final_assessment(self, *, course): ...

    @abstractmethod
    def generate_assist(self, *, target, current_value, instruction, context): ...

    @abstractmethod
    def generate_thumbnail(self, *, prompt): ...


class OpenAIResponsesProvider(CourseAIProvider):
    name = "openai"

    def __init__(self):
        self.api_key = settings.OPENAI_API_KEY
        self.text_model = settings.OPENAI_TEXT_MODEL
        self.image_model = settings.OPENAI_IMAGE_MODEL

    def _post(self, path, payload):
        """One metered provider HTTP call with bounded transient retries.

        Retries begin here because a failed HTTP attempt has no local side
        effects. If those bounded retries are exhausted, the task performs its
        own checkpointed retry without duplicating materialized course data.
        """

        last_failure = None
        for attempt in range(PROVIDER_RETRY_ATTEMPTS):
            _enforce_provider_rate_window()
            response = None
            try:
                response = httpx.post(
                    f"https://api.openai.com/v1/{path}",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                    timeout=PROVIDER_HTTP_TIMEOUT_SECONDS,
                )
            except httpx.TransportError as exc:
                last_failure = exc  # timeouts, connection resets, DNS blips
            else:
                if response.status_code not in RETRYABLE_HTTP_STATUSES:
                    if response.is_success:
                        try:
                            return response.json()
                        except ValueError as exc:
                            raise AIProviderError() from exc
                    # Non-retryable client error (4xx): retrying cannot help.
                    raise AIProviderError()
                last_failure = httpx.HTTPStatusError(
                    f"provider returned HTTP {response.status_code}",
                    request=response.request,
                    response=response,
                )
            if attempt + 1 < PROVIDER_RETRY_ATTEMPTS:
                time.sleep(_retry_delay_seconds(attempt, response))
        raise AIProviderError(retryable=True) from last_failure

    @staticmethod
    def _output_text(data):
        if data.get("output_text"):
            return data["output_text"]
        for output in data.get("output", []):
            for content in output.get("content", []):
                if content.get("type") == "output_text":
                    return content.get("text", "")
        return ""

    def _structured_response(self, *, name, schema, prompt):
        data = self._post(
            "responses",
            {
                "model": self.text_model,
                "store": False,
                "input": prompt,
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": name,
                        "strict": True,
                        "schema": schema,
                    }
                },
            },
        )
        import json

        output_text = self._output_text(data)
        if output_text:
            return json.loads(output_text), data.get("usage", {})
        raise AIProviderError(
            detail="The AI provider returned no course content.", retryable=False
        )

    def generate_course_outline(self, *, title, description, category, topic):
        prompt = f"""Create a professional course outline for the supplied intent.
Category: {category}\nTopic: {topic or 'Not specified'}\nWorking title: {title}\nCreator description: {description}
Use 5-8 modules and 3-5 lessons per module. Return concise course, module, and lesson outlines only. Keep the selected category and topic authoritative."""
        return self._structured_response(
            name="course_outline", schema=COURSE_OUTLINE_SCHEMA, prompt=prompt
        )

    def generate_module_content(self, *, course, module):
        lesson_outline = [
            {
                "title": lesson.title,
                "learning_objectives": lesson.learning_objectives,
                "duration_minutes": lesson.duration_minutes,
            }
            for lesson in module.lessons.order_by("order")
        ]
        prompt = f"""Write the detailed content for one module of a professional course.
Course: {course.title}\nCourse description: {course.description}
Module: {module.title}\nModule description: {module.description}
Lessons, in this exact order: {lesson_outline}
Return exactly one entry per supplied lesson. Each script must be 500-1500 words. Do not create lesson assessments; creators may add those optionally. Include 3-5 explained multiple-choice questions for the module assessment."""
        return self._structured_response(
            name="module_content", schema=MODULE_CONTENT_SCHEMA, prompt=prompt
        )

    def generate_final_assessment(self, *, course):
        module_titles = list(
            course.modules.order_by("order").values_list("title", flat=True)
        )
        prompt = f"""Create the final assessment for this professional course.
Course: {course.title}\nDescription: {course.description}\nModules: {module_titles}
Include at least 15 explained multiple-choice questions spanning the whole course."""
        return self._structured_response(
            name="final_assessment", schema=FINAL_ASSESSMENT_SCHEMA, prompt=prompt
        )

    def generate_assist(self, *, target, current_value, instruction, context):
        data = self._post(
            "responses",
            {
                "model": self.text_model,
                "store": False,
                "input": f"Improve the {target} field for this course. Return only the replacement value.\nCourse context: {context}\nCurrent value: {current_value}\nCreator instruction: {instruction}",
            },
        )
        return self._output_text(data), data.get("usage", {})

    def generate_thumbnail(self, *, prompt):
        data = self._post(
            "images/generations",
            {
                "model": self.image_model,
                "prompt": prompt,
                "size": "1536x1024",
                "response_format": "b64_json",
            },
        )
        return base64.b64decode(data["data"][0]["b64_json"])


def get_course_ai_provider():
    if settings.COURSE_AI_PROVIDER == "openai":
        return OpenAIResponsesProvider()
    raise AIProviderError(
        detail=f"Unsupported COURSE_AI_PROVIDER: {settings.COURSE_AI_PROVIDER}",
        retryable=False,
    )

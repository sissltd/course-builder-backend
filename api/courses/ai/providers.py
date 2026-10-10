import base64
import math
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

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
    "required": [
        "type",
        "question",
        "points",
        "options",
        "correct_index",
        "explanation",
    ],
    "properties": {
        "type": {"type": "string", "const": "MULTIPLE_CHOICE"},
        "question": {"type": "string"},
        "points": {"type": "integer", "minimum": 1},
        "options": {
            "type": "array",
            "minItems": 4,
            "maxItems": 4,
            "items": {"type": "string"},
        },
        "correct_index": {"type": "integer", "minimum": 0, "maximum": 3},
        "explanation": {"type": "string"},
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

# Generation caps the structure below the platform maximums: every module is one
# more sequential provider call, so the upper bound is a cost/latency choice.
GENERATION_MODULE_CAP = 8
GENERATION_LESSONS_PER_MODULE_CAP = 6
LESSON_DURATION_MIN_MINUTES = 5
LESSON_DURATION_MAX_MINUTES = 90


@dataclass(frozen=True)
class GenerationStandards:
    """The submission quality thresholds a generated course must satisfy.

    Built from PlatformSettings - the same row the submission quality check
    reads - so generation and the check can never disagree about the bar.
    """

    course_objectives_min: int
    course_objectives_max: int
    modules_min: int
    modules_max: int
    lessons_per_module_min: int
    lessons_per_module_max: int
    lesson_objectives_min: int
    lesson_objectives_max: int
    description_words_min: int
    description_words_max: int
    script_words_min: int
    script_words_max: int
    duration_min_minutes: int
    duration_max_minutes: int
    final_assessment_min_questions: int

    @classmethod
    def from_platform_settings(
        cls, platform_settings, *, capped: bool = True
    ) -> "GenerationStandards":
        """`capped=False` keeps the platform maximums for non-generated courses."""

        modules_min = platform_settings.course_module_count_min
        lessons_min = platform_settings.course_lessons_per_module_min
        module_cap = GENERATION_MODULE_CAP if capped else math.inf
        lesson_cap = GENERATION_LESSONS_PER_MODULE_CAP if capped else math.inf
        return cls(
            course_objectives_min=platform_settings.course_learning_objectives_min,
            course_objectives_max=platform_settings.course_learning_objectives_max,
            modules_min=modules_min,
            modules_max=max(
                modules_min,
                min(platform_settings.course_module_count_max, module_cap),
            ),
            lessons_per_module_min=lessons_min,
            lessons_per_module_max=max(
                lessons_min,
                min(platform_settings.course_lessons_per_module_max, lesson_cap),
            ),
            lesson_objectives_min=platform_settings.lesson_learning_objectives_min,
            lesson_objectives_max=platform_settings.lesson_learning_objectives_max,
            description_words_min=platform_settings.course_description_word_min,
            description_words_max=platform_settings.course_description_word_max,
            script_words_min=platform_settings.lesson_script_word_min,
            script_words_max=platform_settings.lesson_script_word_max,
            duration_min_minutes=platform_settings.course_duration_min_minutes,
            duration_max_minutes=platform_settings.course_duration_max_minutes,
            final_assessment_min_questions=(
                platform_settings.course_final_assessment_min_questions
            ),
        )

    @property
    def description_words_target(self) -> int:
        """Aim at the lower third of the range: models undershoot word counts."""

        span = self.description_words_max - self.description_words_min
        return self.description_words_min + span // 3

    @property
    def duration_target_minutes(self) -> int:
        return (self.duration_min_minutes + self.duration_max_minutes) // 2


def _objectives_schema(*, scope, min_items, max_items):
    return {
        "type": "array",
        "minItems": min_items,
        "maxItems": max_items,
        "items": {
            "type": "string",
            "description": (
                f"One complete, standalone {scope} objective sentence. "
                "Do not split comma-separated clauses into separate items."
            ),
        },
    }


def _lesson_duration_schema():
    return {
        "type": "integer",
        "minimum": LESSON_DURATION_MIN_MINUTES,
        "maximum": LESSON_DURATION_MAX_MINUTES,
    }


def build_course_outline_schema(standards: GenerationStandards) -> dict:
    return {
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
            "description": {
                "type": "string",
                "description": (
                    f"Learner-facing course description of "
                    f"{standards.description_words_min}-"
                    f"{standards.description_words_max} words."
                ),
            },
            "difficulty_level": {
                "type": "string",
                "enum": ["BEGINNER", "INTERMEDIATE", "ADVANCED"],
            },
            "learning_objectives": _objectives_schema(
                scope="learning",
                min_items=standards.course_objectives_min,
                max_items=standards.course_objectives_max,
            ),
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 3,
                "maxItems": 10,
            },
            "planned_duration_seconds": {
                "type": "integer",
                "minimum": standards.duration_min_minutes * 60,
                "maximum": standards.duration_max_minutes * 60,
            },
            "modules": {
                "type": "array",
                "minItems": standards.modules_min,
                "maxItems": standards.modules_max,
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
                            "items": {
                                "type": "string",
                                "description": (
                                    "One complete, standalone module objective "
                                    "sentence. Do not split comma-separated "
                                    "clauses into separate items."
                                ),
                            },
                        },
                        "lessons": {
                            "type": "array",
                            "minItems": standards.lessons_per_module_min,
                            "maxItems": standards.lessons_per_module_max,
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
                                    "learning_objectives": _objectives_schema(
                                        scope="lesson",
                                        min_items=standards.lesson_objectives_min,
                                        max_items=standards.lesson_objectives_max,
                                    ),
                                    "duration_minutes": _lesson_duration_schema(),
                                },
                            },
                        },
                    },
                },
            },
        },
    }


def build_module_content_schema(
    standards: GenerationStandards, *, lesson_count: int
) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["lessons", "assessment"],
        "properties": {
            "lessons": {
                "type": "array",
                "minItems": lesson_count,
                "maxItems": lesson_count,
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
                        "learning_objectives": _objectives_schema(
                            scope="lesson",
                            min_items=standards.lesson_objectives_min,
                            max_items=standards.lesson_objectives_max,
                        ),
                        "duration_minutes": _lesson_duration_schema(),
                    },
                },
            },
            "assessment": ASSESSMENT_SCHEMA,
        },
    }


def build_final_assessment_schema(standards: GenerationStandards) -> dict:
    return {
        **ASSESSMENT_SCHEMA,
        "properties": {
            **ASSESSMENT_SCHEMA["properties"],
            "questions": {
                **ASSESSMENT_SCHEMA["properties"]["questions"],
                "minItems": standards.final_assessment_min_questions,
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
    def generate_course_outline(
        self, *, title, description, category, topic, standards
    ): ...

    @abstractmethod
    def generate_module_content(self, *, course, module, standards): ...

    @abstractmethod
    def generate_final_assessment(self, *, course, standards): ...

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

    def structured_response(self, *, name, schema, prompt, images=()):
        """A strict JSON-schema response and the provider's token usage.

        Public for other engines (the Production Engine's storyboard and
        visual check) that share this provider's metering, retries and rate
        window. `images` are data URLs (e.g. "data:image/jpeg;base64,...")
        sent with the prompt for the model to look at.
        """

        return self._structured_response(name=name, schema=schema, prompt=prompt, images=images)

    def _structured_response(self, *, name, schema, prompt, images=()):
        content = prompt
        if images:
            content = [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": prompt}]
                    + [{"type": "input_image", "image_url": image, "detail": "low"} for image in images],
                }
            ]
        data = self._post(
            "responses",
            {
                "model": self.text_model,
                "store": False,
                "input": content,
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

    def generate_course_outline(
        self, *, title, description, category, topic, standards
    ):
        prompt = f"""Create a professional course outline for the supplied intent.
Category: {category}\nTopic: {topic or 'Not specified'}\nWorking title: {title}\nCreator description: {description}
The course must pass these submission standards exactly:
- Course description: {standards.description_words_min}-{standards.description_words_max} words (aim for about {standards.description_words_target} words). Expand the creator description into a full learner-facing overview: who it is for, what they will learn, and the outcome.
- Course learning objectives: {standards.course_objectives_min}-{standards.course_objectives_max} items.
- Modules: {standards.modules_min}-{standards.modules_max}, each with {standards.lessons_per_module_min}-{standards.lessons_per_module_max} lessons.
- Each lesson: {standards.lesson_objectives_min}-{standards.lesson_objectives_max} learning objectives.
- The duration_minutes of all lessons combined must total {standards.duration_min_minutes}-{standards.duration_max_minutes} minutes (aim for about {standards.duration_target_minutes}); planned_duration_seconds must equal that total in seconds.
Return concise course, module, and lesson outlines only. Keep the selected category and topic authoritative. Each learning_objectives array item must be a complete standalone sentence; do not split one objective into separate items at commas."""
        return self._structured_response(
            name="course_outline",
            schema=build_course_outline_schema(standards),
            prompt=prompt,
        )

    def generate_module_content(self, *, course, module, standards):
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
Return exactly one entry per supplied lesson. Each script must be {standards.script_words_min}-{standards.script_words_max} words. Each lesson must keep {standards.lesson_objectives_min}-{standards.lesson_objectives_max} learning objectives, and its duration_minutes must stay at the supplied value so the course total stays within {standards.duration_min_minutes}-{standards.duration_max_minutes} minutes. Do not create lesson assessments; creators may add those optionally. Include 3-5 explained multiple-choice questions for the module assessment."""
        return self._structured_response(
            name="module_content",
            schema=build_module_content_schema(
                standards, lesson_count=len(lesson_outline)
            ),
            prompt=prompt,
        )

    def generate_final_assessment(self, *, course, standards):
        module_titles = list(
            course.modules.order_by("order").values_list("title", flat=True)
        )
        prompt = f"""Create the final assessment for this professional course.
Course: {course.title}\nDescription: {course.description}\nModules: {module_titles}
Include at least {standards.final_assessment_min_questions} explained multiple-choice questions spanning the whole course."""
        return self._structured_response(
            name="final_assessment",
            schema=build_final_assessment_schema(standards),
            prompt=prompt,
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

"""Plans a lesson's video as a sequence of faceless scenes.

The script is split into sentences here, deterministically, and the model
only groups consecutive sentences into scenes and describes what each scene
shows. It never writes narration, so the approved words reach the video
unchanged; a plan that skips, repeats or reorders a sentence is rejected.
"""

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

from django.conf import settings

from api.courses.ai.providers import AIProviderError, get_course_ai_provider
from api.production.enums import SceneType

#: Sentence boundary: end punctuation, optional closing quote or bracket,
#: then whitespace. Simple on purpose - a sentence is the smallest unit a
#: scene can hold, so an imperfect split only makes scenes coarser.
SENTENCE_BOUNDARY = re.compile(r"(?:(?<=[.!?])|(?<=[.!?][\"')\]]))\s+")

SCENE_TEXT_MAX_CHARS = 120
"""On-screen text is a signal, not a transcript (Mayer's redundancy principle)."""

SCENE_BULLETS_MAX = 6
BULLET_MAX_CHARS = 80


def split_sentences(script: str) -> list[str]:
    return [part.strip() for part in SENTENCE_BOUNDARY.split(script.strip()) if part.strip()]


@dataclass(frozen=True)
class PlannedScene:
    first_sentence: int
    last_sentence: int
    scene_type: str
    on_screen_text: str
    visual_brief: str
    bullets: tuple[str, ...] = ()
    code: str = ""


@dataclass(frozen=True)
class StoryboardResult:
    scenes: list[PlannedScene]
    input_tokens: int
    output_tokens: int


class StoryboardRejected(AIProviderError):
    """The model's plan does not cover the script exactly. Retryable: a fresh
    attempt usually gets it right."""

    def __init__(self, detail):
        super().__init__(retryable=True, detail=detail)


STORYBOARD_PROMPT_VERSION = "storyboard-2"
"""Sent with every trace, so scores can be compared across prompt changes.
Change it whenever the prompt below changes."""


def storyboard_schema(*, allow_broll: bool) -> dict:
    """The plan's JSON Schema; BROLL is offered only when b-roll is on."""

    types = [value for value in SceneType.values if allow_broll or value != SceneType.BROLL]
    schema = json.loads(json.dumps(STORYBOARD_SCHEMA))
    schema["properties"]["scenes"]["items"]["properties"]["scene_type"]["enum"] = types
    return schema


STORYBOARD_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["scenes"],
    "properties": {
        "scenes": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "first_sentence",
                    "last_sentence",
                    "scene_type",
                    "on_screen_text",
                    "visual_brief",
                    "bullets",
                    "code",
                ],
                "properties": {
                    "first_sentence": {"type": "integer", "minimum": 0},
                    "last_sentence": {"type": "integer", "minimum": 0},
                    "scene_type": {"type": "string", "enum": SceneType.values},
                    "on_screen_text": {"type": "string", "maxLength": SCENE_TEXT_MAX_CHARS},
                    "visual_brief": {"type": "string"},
                    "bullets": {
                        "type": "array",
                        "maxItems": SCENE_BULLETS_MAX,
                        "items": {"type": "string", "maxLength": BULLET_MAX_CHARS},
                    },
                    "code": {"type": "string"},
                },
            },
        }
    },
}


def check_coverage(scenes: list[PlannedScene], sentence_count: int) -> None:
    """Raise StoryboardRejected unless the scenes cover sentences
    0..sentence_count-1 exactly once, in order."""

    expected = 0
    for scene in scenes:
        if scene.first_sentence != expected or scene.last_sentence < scene.first_sentence:
            raise StoryboardRejected(
                f"Storyboard does not cover the script in order at sentence {expected}."
            )
        expected = scene.last_sentence + 1
    if expected != sentence_count:
        raise StoryboardRejected(
            f"Storyboard covers {expected} of {sentence_count} sentences."
        )


class StoryboardProvider(ABC):
    @abstractmethod
    def plan_lesson(
        self, *, course_title: str, lesson_title: str, sentences: list[str], broll_limit: int = 0
    ) -> StoryboardResult: ...


def limit_broll(scenes: list[PlannedScene], limit: int) -> list[PlannedScene]:
    """Scenes past the b-roll allowance become illustrations of the same brief."""

    kept, out = 0, []
    for scene in scenes:
        if scene.scene_type == SceneType.BROLL:
            if kept >= limit:
                scene = PlannedScene(**{**scene.__dict__, "scene_type": SceneType.IMAGE})
            else:
                kept += 1
        out.append(scene)
    return out


class AIStoryboardProvider(StoryboardProvider):
    """Uses the course AI provider, sharing its retries and rate window."""

    def plan_lesson(self, *, course_title, lesson_title, sentences, broll_limit=0):
        numbered = "\n".join(f"[{index}] {text}" for index, text in enumerate(sentences))
        prompt = (
            "You are storyboarding a faceless instructional video: no presenter "
            "or person on screen, only slides, diagrams, code, illustrations and "
            "short text. Group the numbered narration sentences of this lesson "
            "into consecutive scenes, each covering roughly 15-40 seconds of "
            "speech. Every sentence belongs to exactly one scene, in order, "
            "starting at sentence 0. Do not rewrite the narration. For each "
            "scene choose a scene_type, write on-screen text of at most "
            f"{SCENE_TEXT_MAX_CHARS} characters that signals the key point "
            "rather than repeating the narration, and a visual_brief describing "
            "what the scene shows. Fill bullets for BULLETS and RECAP (key "
            f"points, at most {SCENE_BULLETS_MAX}, each under {BULLET_MAX_CHARS} "
            "characters), DIAGRAM (the steps of the flow, in order) and "
            "COMPARISON (first row the two column headings, then one row per "
            "point, each written 'left | right'); leave it empty otherwise. "
            "Fill code only for CODE scenes, with the code itself (at most 16 "
            "lines). For IMAGE scenes the visual_brief is the illustration "
            "prompt: describe objects or concepts, never people's faces.\n\n"
            + (
                f"You may use up to {broll_limit} BROLL scenes, for moments that "
                "benefit from motion footage: the visual_brief is the clip prompt, "
                "describing places, objects or processes in motion, never people. "
                "Prefer still scenes for anything that needs exact text, code or "
                "diagrams.\n\n"
                if broll_limit
                else ""
            )
            + f"Course: {course_title}\nLesson: {lesson_title}\n\nSentences:\n{numbered}"
        )
        data, usage = get_course_ai_provider().structured_response(
            name="lesson_storyboard", schema=storyboard_schema(allow_broll=broll_limit > 0), prompt=prompt
        )
        scenes = limit_broll(
            [PlannedScene(**{**scene, "bullets": tuple(scene["bullets"])}) for scene in data["scenes"]],
            broll_limit,
        )
        check_coverage(scenes, len(sentences))
        return StoryboardResult(
            scenes=scenes,
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
        )


def get_storyboard_provider() -> StoryboardProvider:
    if settings.COURSE_AI_PROVIDER == "openai":
        return AIStoryboardProvider()
    raise AIProviderError(
        detail=f"Unsupported COURSE_AI_PROVIDER: {settings.COURSE_AI_PROVIDER}",
        retryable=False,
    )

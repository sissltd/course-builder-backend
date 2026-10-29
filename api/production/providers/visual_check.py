"""The visual check: a vision model looks at one frame of every scene of a
finished lesson, as rendered, and says what is wrong with it.

It catches what measuring the file cannot: on-screen text that is cut off,
overlapping, misspelt against what the scene was meant to say, or
unreadable; an illustration or clip with people, faces or stray text in it
(the video is faceless); a blank or broken frame.
"""

import base64
from dataclasses import dataclass

from api.courses.ai.providers import get_course_ai_provider

VISUAL_CHECK_PROMPT_VERSION = "visual-check-1"
FRAMES_PER_CALL = 8
"""Frames looked at in one model call; a lesson with more is checked in parts."""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["frames"],
    "properties": {
        "frames": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["index", "ok", "problems"],
                "properties": {
                    "index": {"type": "integer"},
                    "ok": {"type": "boolean"},
                    "problems": {"type": "array", "items": {"type": "string"}},
                },
            },
        }
    },
}


@dataclass(frozen=True)
class Frame:
    jpeg: bytes
    scene_type: str
    on_screen_text: str
    bullets: list[str]


@dataclass(frozen=True)
class Verdict:
    problems: dict[int, list[str]]
    """Problems by frame index; frames that passed are absent."""
    input_tokens: int
    output_tokens: int


def _describe(index: int, frame: Frame) -> str:
    expected = frame.on_screen_text or "(none)"
    points = "; ".join(frame.bullets) or "(none)"
    return f"Frame {index}: a {frame.scene_type} scene. Heading: {expected}. Points: {points}."


def check(frames: list[Frame]) -> Verdict:
    problems: dict[int, list[str]] = {}
    input_tokens = output_tokens = 0
    for start in range(0, len(frames), FRAMES_PER_CALL):
        batch = list(enumerate(frames[start : start + FRAMES_PER_CALL], start=start))
        prompt = (
            "You are the visual quality check for a faceless online-course video. "
            "Each image is one scene, in the order listed. For each, report "
            "problems a learner would notice: text cut off, overlapping or too "
            "small to read; text that differs from or misspells what the scene "
            "should show; garbled or nonsense text; any person, face or hands; "
            "stray text or logos inside an illustration or footage; a blank, "
            "black or corrupted frame. Do not judge style or taste. Mark ok "
            "true with no problems when the frame is fine.\n\n"
            + "\n".join(_describe(index, frame) for index, frame in batch)
        )
        images = [f"data:image/jpeg;base64,{base64.b64encode(frame.jpeg).decode()}" for _, frame in batch]
        data, usage = get_course_ai_provider().structured_response(
            name="visual_check", schema=SCHEMA, prompt=prompt, images=images
        )
        input_tokens += int(usage.get("input_tokens", 0))
        output_tokens += int(usage.get("output_tokens", 0))
        known = {index for index, _ in batch}
        for verdict in data["frames"]:
            if verdict["index"] in known and not verdict["ok"] and verdict["problems"]:
                problems[verdict["index"]] = verdict["problems"]
    return Verdict(problems=problems, input_tokens=input_tokens, output_tokens=output_tokens)

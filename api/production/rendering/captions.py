"""Captions and transcripts from the narration's own timing.

The voice provider reports when each character of the text it was given is
spoken. Captions are cut from the approved narration (never from what was
sent to the voice, which may carry pronunciation respellings), and each cue
is timed by mapping its characters onto the spoken text proportionally.
With no respellings the two texts are the same and the timing is exact.
"""

import re
from dataclasses import dataclass

CUE_MAX_CHARS = 84
"""Two lines of 42 characters, the common broadcast caption limit."""

LINE_MAX_CHARS = 42


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    text: str


def even_alignment(text: str, duration: float) -> list[list[float]]:
    """Per-character timing spread evenly over `duration`, for voices that
    report none. Captions stay in step at sentence level."""

    count = max(len(text), 1)
    step = duration / count
    return [[index * step, (index + 1) * step] for index in range(len(text))]


def _chunks(text: str) -> list[tuple[int, int]]:
    """Character spans of caption-sized pieces of `text`: sentences, split
    further at word boundaries when longer than CUE_MAX_CHARS."""

    spans = []
    for sentence in re.finditer(r"\S.*?(?:[.!?][\"')\]]?(?=\s|$)|$)", text, flags=re.S):
        start, end = sentence.span()
        while end - start > CUE_MAX_CHARS:
            cut = text.rfind(" ", start, start + CUE_MAX_CHARS)
            if cut <= start:
                cut = start + CUE_MAX_CHARS
            spans.append((start, cut))
            start = cut + 1
        if end > start:
            spans.append((start, end))
    return spans


def cues_for(
    *, text: str, spoken: str, alignment: list[list[float]], offset: float
) -> list[Cue]:
    """Caption cues for one scene's narration `text`, which was voiced as
    `spoken` with per-character `alignment`, starting `offset` seconds into
    the lesson."""

    if not text.strip() or not alignment:
        return []
    scale = len(alignment) / max(len(text), 1)
    last = len(alignment) - 1
    cues = []
    for start, end in _chunks(text):
        first_char = min(int(start * scale), last)
        last_char = min(max(int((end - 1) * scale), first_char), last)
        cues.append(
            Cue(
                start=offset + alignment[first_char][0],
                end=offset + alignment[last_char][1],
                text=_wrap(text[start:end].strip()),
            )
        )
    return cues


def _wrap(text: str) -> str:
    if len(text) <= LINE_MAX_CHARS:
        return text
    middle = len(text) // 2
    left = text.rfind(" ", 0, middle + 1)
    right = text.find(" ", middle)
    candidates = [cut for cut in (left, right) if cut > 0]
    if not candidates:
        return text
    cut = min(candidates, key=lambda position: abs(position - middle))
    return f"{text[:cut]}\n{text[cut + 1:]}"


def _timestamp(seconds: float, separator: str) -> str:
    millis = max(int(round(seconds * 1000)), 0)
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{millis:03d}"


def to_srt(cues: list[Cue]) -> str:
    blocks = [
        f"{index}\n{_timestamp(cue.start, ',')} --> {_timestamp(cue.end, ',')}\n{cue.text}\n"
        for index, cue in enumerate(cues, start=1)
    ]
    return "\n".join(blocks)


def to_vtt(cues: list[Cue]) -> str:
    blocks = [
        f"{_timestamp(cue.start, '.')} --> {_timestamp(cue.end, '.')}\n{cue.text}\n" for cue in cues
    ]
    return "WEBVTT\n\n" + "\n".join(blocks)

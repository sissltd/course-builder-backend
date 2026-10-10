"""Draws a storyboard scene as a 1920x1080 frame.

Deterministic on purpose: the same scene always draws the same picture, so
a frame is stored once under the hash of its inputs and a rework that
changes one scene redraws only that scene. Light background, because the
quality check's black-frame detector would flag a dark theme.
"""

import io
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from api.production.enums import SceneType

TEMPLATE_VERSION = "slides-1"
"""Part of every frame's hash: changing the look below must change this."""

WIDTH, HEIGHT = 1920, 1080
THUMBNAIL_SIZE = (1280, 720)
MARGIN = 120

BACKGROUND = (248, 250, 252)
INK = (15, 23, 42)
MUTED = (71, 85, 105)
ACCENT = (37, 99, 235)
PANEL = (226, 232, 240)
CODE_BACKGROUND = (30, 41, 59)
CODE_INK = (226, 232, 240)

#: Font files tried in order: the Debian package the image installs
#: (fonts-dejavu-core), then macOS system fonts for local runs. Pillow's
#: bundled font is the last resort.
FONT_CANDIDATES = {
    "regular": (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ),
    "bold": (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ),
    "mono": (
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/System/Library/Fonts/Supplemental/Courier New.ttf",
    ),
}

CODE_MAX_LINES = 16
BULLETS_MAX = 6


@lru_cache(maxsize=64)
def _font(style: str, size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES[style]:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        line = ""
        for word in paragraph.split():
            candidate = f"{line} {word}".strip()
            if draw.textlength(candidate, font=font) <= width or not line:
                line = candidate
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return lines


def _text_block(draw, xy, text, font, width, fill, spacing=1.25, max_lines=None) -> int:
    """Draw wrapped text from `xy`; returns the y below it."""

    x, y = xy
    lines = _wrap(draw, text, font, width)
    if max_lines is not None and len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(".") + "…"
    line_height = int(font.size * spacing)
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += line_height
    return y


def _frame(lesson_title: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, WIDTH, 12), fill=ACCENT)
    if lesson_title:
        draw.text((MARGIN, 56), lesson_title.upper(), font=_font("bold", 28), fill=MUTED)
    return image, draw


def _headline(draw, text: str, y: int = 130) -> int:
    if not text:
        return y
    return _text_block(draw, (MARGIN, y), text, _font("bold", 64), WIDTH - 2 * MARGIN, INK, max_lines=2) + 30


def _bullets(draw, items: list[str], y: int) -> None:
    font = _font("regular", 46)
    for item in items[:BULLETS_MAX]:
        draw.ellipse((MARGIN, y + 18, MARGIN + 18, y + 36), fill=ACCENT)
        y = _text_block(draw, (MARGIN + 50, y), item, font, WIDTH - 2 * MARGIN - 50, INK, max_lines=2) + 24


def _diagram(draw, steps: list[str], y: int) -> None:
    steps = steps[:BULLETS_MAX] or ["…"]
    per_row = 3 if len(steps) > 3 else len(steps)
    rows = [steps[index : index + per_row] for index in range(0, len(steps), per_row)]
    gap = 90
    box_width = (WIDTH - 2 * MARGIN - gap * (per_row - 1)) // per_row
    box_height = 220
    font = _font("bold", 40)
    for row_index, row in enumerate(rows):
        top = y + row_index * (box_height + 80)
        for index, step in enumerate(row):
            left = MARGIN + index * (box_width + gap)
            draw.rounded_rectangle((left, top, left + box_width, top + box_height), radius=24, fill=PANEL, outline=ACCENT, width=4)
            lines = _wrap(draw, step, font, box_width - 40)[:3]
            text_top = top + (box_height - len(lines) * 50) // 2
            for line in lines:
                line_width = draw.textlength(line, font=font)
                draw.text((left + (box_width - line_width) / 2, text_top), line, font=font, fill=INK)
                text_top += 50
            if index < len(row) - 1:
                arrow_y = top + box_height // 2
                start = left + box_width + 12
                end = start + gap - 24
                draw.line((start, arrow_y, end, arrow_y), fill=ACCENT, width=6)
                draw.polygon([(end + 8, arrow_y), (end - 14, arrow_y - 14), (end - 14, arrow_y + 14)], fill=ACCENT)


def _code(draw, code: str, y: int) -> None:
    lines = code.splitlines()[:CODE_MAX_LINES] or [""]
    font = _font("mono", 34)
    line_height = 46
    bottom = min(y + 60 + len(lines) * line_height, HEIGHT - 80)
    draw.rounded_rectangle((MARGIN, y, WIDTH - MARGIN, bottom), radius=20, fill=CODE_BACKGROUND)
    text_y = y + 30
    max_width = WIDTH - 2 * MARGIN - 80
    for line in lines:
        while line and draw.textlength(line, font=font) > max_width:
            line = line[:-2] + "…"
        draw.text((MARGIN + 40, text_y), line, font=font, fill=CODE_INK)
        text_y += line_height


def _comparison(draw, rows: list[str], y: int) -> None:
    pairs = [(row.split("|", 1) + [""])[:2] for row in rows[: BULLETS_MAX + 1]]
    column_width = (WIDTH - 2 * MARGIN - 60) // 2
    for index, (left, right) in enumerate(pairs):
        header = index == 0
        font = _font("bold" if header else "regular", 44 if header else 40)
        for column, text in enumerate((left.strip(), right.strip())):
            x = MARGIN + column * (column_width + 60)
            if header:
                draw.rounded_rectangle((x, y - 10, x + column_width, y + 66), radius=14, fill=ACCENT)
            _text_block(draw, (x + 24, y), text, font, column_width - 48, BACKGROUND if header else INK, max_lines=2)
        y += 110


def _title(draw, heading: str, subtitle: str) -> None:
    font = _font("bold", 88)
    lines = _wrap(draw, heading, font, WIDTH - 2 * MARGIN)[:3]
    y = HEIGHT // 2 - len(lines) * 55 - 40
    for line in lines:
        draw.text(((WIDTH - draw.textlength(line, font=font)) / 2, y), line, font=font, fill=INK)
        y += 110
    if subtitle:
        sub_font = _font("regular", 44)
        for line in _wrap(draw, subtitle, sub_font, WIDTH - 2 * MARGIN)[:2]:
            draw.text(((WIDTH - draw.textlength(line, font=sub_font)) / 2, y + 30), line, font=sub_font, fill=MUTED)
            y += 56


def _png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def render_scene(
    *,
    scene_type: str,
    lesson_title: str,
    on_screen_text: str,
    bullets: list[str],
    code: str,
    narration: str,
    illustration: bytes | None = None,
) -> bytes:
    """One scene as PNG bytes. A scene with nothing to list falls back to its
    narration's first sentence, so no frame is ever blank."""

    image, draw = _frame(lesson_title)
    items = [item for item in bullets if item.strip()]
    if scene_type == SceneType.TITLE:
        _title(draw, on_screen_text or lesson_title, lesson_title if on_screen_text else "")
    elif scene_type == SceneType.QUOTE:
        quote = on_screen_text or narration
        draw.text((MARGIN, 220), "“", font=_font("bold", 200), fill=ACCENT)
        _text_block(draw, (MARGIN + 60, 380), quote, _font("bold", 60), WIDTH - 2 * MARGIN - 120, INK, max_lines=5)
    else:
        y = _headline(draw, on_screen_text)
        if scene_type == SceneType.CODE and code.strip():
            _code(draw, code, y)
        elif scene_type == SceneType.DIAGRAM and items:
            _diagram(draw, items, y + 40)
        elif scene_type == SceneType.COMPARISON and items:
            _comparison(draw, items, y + 20)
        elif scene_type == SceneType.IMAGE and illustration:
            picture = Image.open(io.BytesIO(illustration)).convert("RGB")
            picture.thumbnail((WIDTH - 2 * MARGIN, HEIGHT - y - 80))
            image.paste(picture, ((WIDTH - picture.width) // 2, y))
        elif items:
            if scene_type == SceneType.RECAP and not on_screen_text:
                y = _headline(draw, "Recap")
            _bullets(draw, items, y + 10)
        else:
            first_sentence = narration.split(". ")[0].strip()
            _text_block(draw, (MARGIN, y + 20), first_sentence, _font("regular", 50), WIDTH - 2 * MARGIN, MUTED, max_lines=5)
    return _png(image)


def render_card(*, heading: str, subtitle: str, size: tuple[int, int] = (WIDTH, HEIGHT)) -> bytes:
    """A title card: the trailer's frames and the course thumbnail."""

    image, draw = _frame("")
    _title(draw, heading, subtitle)
    if size != (WIDTH, HEIGHT):
        image = image.resize(size, Image.LANCZOS)
    return _png(image)

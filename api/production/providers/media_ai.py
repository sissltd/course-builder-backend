"""Illustrations and transcription, both from the course AI provider's
vendor (OpenAI), metered per call."""

import time
from decimal import Decimal

import httpx
from django.conf import settings

from api.courses.ai.providers import AIProviderError, get_course_ai_provider

TRANSCRIBE_TIMEOUT_SECONDS = 300
RETRY_ATTEMPTS = 3
RETRYABLE_STATUSES = {408, 409, 429, 500, 502, 503, 504}

ILLUSTRATION_STYLE = (
    "Clean flat vector illustration for an online course slide, light "
    "background, no text, no logos, no people's faces. Subject: "
)


def illustrate(brief: str) -> tuple[bytes, Decimal]:
    """An illustration for an IMAGE scene and what it cost."""

    image = get_course_ai_provider().generate_thumbnail(prompt=ILLUSTRATION_STYLE + brief)
    return image, Decimal(settings.PRODUCTION_IMAGE_USD_PER_IMAGE)


def transcribe(audio: bytes, *, seconds: float) -> tuple[str, Decimal]:
    """What a speech-to-text model hears in `audio` (MP3), and its cost.

    Used only to score caption accuracy: the captions themselves come from
    the approved script, never from this transcript.
    """

    last = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            response = httpx.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
                data={"model": settings.PRODUCTION_TRANSCRIBE_MODEL, "response_format": "json"},
                files={"file": ("speech.mp3", audio, "audio/mpeg")},
                timeout=TRANSCRIBE_TIMEOUT_SECONDS,
            )
        except httpx.TransportError as exc:
            last = str(exc)
        else:
            if response.is_success:
                cost = Decimal(str(seconds)) / 60 * Decimal(settings.PRODUCTION_TRANSCRIBE_USD_PER_MINUTE)
                return response.json().get("text", ""), cost
            if response.status_code not in RETRYABLE_STATUSES:
                raise AIProviderError(
                    detail=f"Transcription refused the request (HTTP {response.status_code}).",
                    retryable=False,
                )
            last = f"HTTP {response.status_code}"
        if attempt + 1 < RETRY_ATTEMPTS:
            time.sleep(2**attempt)
    raise AIProviderError(detail=f"Transcription unavailable ({last}).", retryable=True)

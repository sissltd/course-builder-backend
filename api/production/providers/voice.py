"""Narration voices: ElevenLabs first, Google Chirp 3 HD as the fallback.

Both are English stock voices; no voice is cloned. ElevenLabs reports when
each character is spoken, which times the captions exactly; Google reports
nothing, so its captions are timed evenly across each scene (still in step
at sentence level, since every scene is voiced on its own).
"""

import base64
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal

import httpx
from django.conf import settings

from api.courses.ai.providers import AIProviderError

HTTP_TIMEOUT_SECONDS = 120
RETRY_ATTEMPTS = 3
RETRYABLE_STATUSES = {408, 409, 429, 500, 502, 503, 504}
CONTEXT_CHARS = 300
"""How much neighbouring narration is sent as context, for natural flow
across scene boundaries (ElevenLabs previous_text / next_text)."""


@dataclass(frozen=True)
class VoiceResult:
    audio: bytes
    """MP3."""
    alignment: list[list[float]] | None
    """[start, end] seconds for each character of the text sent, or None."""
    cost: Decimal


class VoiceProvider(ABC):
    name: str
    voice: str

    @abstractmethod
    def synthesize(self, *, text: str, previous_text: str, next_text: str) -> VoiceResult: ...


def _post(url: str, *, headers: dict, payload: dict) -> dict:
    last = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            response = httpx.post(url, headers=headers, json=payload, timeout=HTTP_TIMEOUT_SECONDS)
        except httpx.TransportError as exc:
            last = str(exc)
        else:
            if response.is_success:
                return response.json()
            if response.status_code not in RETRYABLE_STATUSES:
                raise AIProviderError(
                    detail=f"Voice provider refused the request (HTTP {response.status_code}).",
                    retryable=False,
                )
            last = f"HTTP {response.status_code}"
        if attempt + 1 < RETRY_ATTEMPTS:
            time.sleep(2**attempt)
    raise AIProviderError(detail=f"Voice provider unavailable ({last}).", retryable=True)


class ElevenLabsVoice(VoiceProvider):
    name = "ElevenLabs"

    def __init__(self):
        self.voice = settings.ELEVENLABS_VOICE_ID
        self.model = settings.ELEVENLABS_MODEL_ID

    def synthesize(self, *, text, previous_text, next_text):
        data = _post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice}/with-timestamps"
            "?output_format=mp3_44100_128",
            headers={"xi-api-key": settings.ELEVENLABS_API_KEY},
            payload={
                "text": text,
                "model_id": self.model,
                "previous_text": previous_text[-CONTEXT_CHARS:],
                "next_text": next_text[:CONTEXT_CHARS],
            },
        )
        alignment = data.get("alignment") or {}
        starts = alignment.get("character_start_times_seconds") or []
        ends = alignment.get("character_end_times_seconds") or []
        return VoiceResult(
            audio=base64.b64decode(data["audio_base64"]),
            alignment=[[start, end] for start, end in zip(starts, ends, strict=False)] or None,
            cost=Decimal(len(text)) * Decimal(settings.PRODUCTION_VOICE_USD_PER_1K_CHARS) / 1000,
        )


class GoogleChirpVoice(VoiceProvider):
    name = "Google TTS"

    def __init__(self):
        self.voice = settings.GOOGLE_TTS_VOICE

    def synthesize(self, *, text, previous_text, next_text):
        data = _post(
            "https://texttospeech.googleapis.com/v1/text:synthesize",
            headers={"X-Goog-Api-Key": settings.GOOGLE_TTS_API_KEY},
            payload={
                "input": {"text": text},
                "voice": {"languageCode": self.voice[:5], "name": self.voice},
                "audioConfig": {"audioEncoding": "MP3", "sampleRateHertz": 44100},
            },
        )
        return VoiceResult(
            audio=base64.b64decode(data["audioContent"]),
            alignment=None,
            cost=Decimal(len(text)) * Decimal(settings.PRODUCTION_FALLBACK_VOICE_USD_PER_1M_CHARS) / 1_000_000,
        )


def get_voice_providers() -> list[VoiceProvider]:
    """The configured voices, primary first. A voice without an API key is
    left out; none configured is a non-retryable failure."""

    voices: list[VoiceProvider] = []
    if settings.ELEVENLABS_API_KEY:
        voices.append(ElevenLabsVoice())
    if settings.GOOGLE_TTS_API_KEY:
        voices.append(GoogleChirpVoice())
    if not voices:
        raise AIProviderError(
            detail="No narration voice is configured (ELEVENLABS_API_KEY or GOOGLE_TTS_API_KEY).",
            retryable=False,
        )
    return voices

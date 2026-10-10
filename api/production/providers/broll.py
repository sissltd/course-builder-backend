"""AI b-roll: short generated motion clips, from Veo through the Gemini API.

A clip is asked for with people generation switched off, so b-roll keeps
the video faceless. Generation is asynchronous: the request returns an
operation, which is polled until the clip is ready, then downloaded. The
clip's own sound is discarded; the narration plays over it.
"""

import time
from dataclasses import dataclass
from decimal import Decimal

import httpx
from django.conf import settings

from api.courses.ai.providers import AIProviderError

API = "https://generativelanguage.googleapis.com/v1beta"
CLIP_SECONDS = 8
POLL_EVERY_SECONDS = 10
POLL_LIMIT_SECONDS = 10 * 60
HTTP_TIMEOUT_SECONDS = 120
RETRYABLE_STATUSES = {408, 429, 500, 502, 503, 504}

BROLL_STYLE = (
    "Cinematic, clean, well-lit b-roll for an online course. No people, no "
    "faces, no hands, no on-screen text, no logos. Slow, steady camera. Subject: "
)


@dataclass(frozen=True)
class Clip:
    video: bytes
    """MP4."""
    seconds: int
    cost: Decimal


def is_configured() -> bool:
    return bool(settings.GEMINI_API_KEY)


def _request(method: str, url: str, **kwargs) -> httpx.Response:
    try:
        response = httpx.request(
            method, url, headers={"x-goog-api-key": settings.GEMINI_API_KEY}, timeout=HTTP_TIMEOUT_SECONDS,
            follow_redirects=True, **kwargs,
        )
    except httpx.TransportError as exc:
        raise AIProviderError(detail=f"Veo is unreachable: {exc}", retryable=True) from exc
    if response.status_code in RETRYABLE_STATUSES:
        raise AIProviderError(detail=f"Veo answered HTTP {response.status_code}.", retryable=True)
    if not response.is_success:
        raise AIProviderError(detail=f"Veo refused the request (HTTP {response.status_code}).", retryable=False)
    return response


def generate(brief: str) -> Clip:
    """One faceless 16:9 clip of CLIP_SECONDS for `brief`."""

    operation = _request(
        "POST",
        f"{API}/models/{settings.VEO_MODEL}:predictLongRunning",
        json={
            "instances": [{"prompt": BROLL_STYLE + brief}],
            "parameters": {
                "aspectRatio": "16:9",
                "durationSeconds": CLIP_SECONDS,
                "personGeneration": "dont_allow",
            },
        },
    ).json()
    waited = 0
    while not operation.get("done"):
        if waited >= POLL_LIMIT_SECONDS:
            raise AIProviderError(detail="Veo did not finish the clip in time.", retryable=True)
        time.sleep(POLL_EVERY_SECONDS)
        waited += POLL_EVERY_SECONDS
        operation = _request("GET", f"{API}/{operation['name']}").json()
    if operation.get("error"):
        raise AIProviderError(detail=f"Veo could not make the clip: {operation['error'].get('message', '')}", retryable=False)
    samples = operation.get("response", {}).get("generateVideoResponse", {}).get("generatedSamples") or []
    if not samples:
        # Veo returns no sample when its safety filters block the prompt.
        raise AIProviderError(detail="Veo returned no clip (the prompt was filtered).", retryable=False)
    video = _request("GET", samples[0]["video"]["uri"]).content
    return Clip(video=video, seconds=CLIP_SECONDS, cost=Decimal(settings.PRODUCTION_BROLL_USD_PER_SECOND) * CLIP_SECONDS)

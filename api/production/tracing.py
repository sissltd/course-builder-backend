"""Evaluation tracing: every model and vendor call a run makes, and the
quality scores of what it produced, sent to Langfuse.

One Langfuse trace per run (its id is the run's id). Each call is a
`generation` (model, what went in, what came out, tokens or units, cost,
time) and each finished lesson gets `score`s: caption accuracy, loudness,
whether the storyboard covered the script first time, whether the visual
check passed. Prompt versions travel in the metadata, so a prompt change
can be compared against the scores it produced.

Best effort by design: tracing is off unless LANGFUSE_PUBLIC_KEY and
LANGFUSE_SECRET_KEY are set, events are sent in batches, and a Langfuse
outage is logged and ignored - it never fails or slows a run beyond one
short timeout per batch. Course text is sent with each call, so point
LANGFUSE_HOST at a self-hosted instance if that must stay in-house.
"""

import logging
import uuid
from datetime import UTC, datetime

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

FLUSH_TIMEOUT_SECONDS = 10
FLUSH_AT_EVENTS = 50
TEXT_LIMIT = 4000
"""Long inputs (a whole lesson script) are cut for the trace."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _short(value):
    if isinstance(value, str) and len(value) > TEXT_LIMIT:
        return value[:TEXT_LIMIT] + "…"
    return value


class Tracer:
    """Collects one run's events and sends them in batches."""

    def __init__(self, *, run):
        self.enabled = bool(settings.LANGFUSE_PUBLIC_KEY and settings.LANGFUSE_SECRET_KEY)
        self.trace_id = str(run.id)
        self.events: list[dict] = []
        if self.enabled:
            self._add(
                "trace-create",
                {
                    "id": self.trace_id,
                    "name": f"production-{run.kind.lower()}",
                    "timestamp": _now(),
                    "metadata": {"course_id": str(run.course_id), "course_title": run.course.title},
                    "tags": ["production-engine", run.kind],
                },
            )

    def _add(self, kind: str, body: dict) -> None:
        self.events.append({"id": str(uuid.uuid4()), "timestamp": _now(), "type": kind, "body": body})
        if len(self.events) >= FLUSH_AT_EVENTS:
            self.flush()

    def generation(
        self,
        *,
        name: str,
        model: str,
        started_at: datetime,
        input=None,
        output=None,
        usage: dict | None = None,
        cost=None,
        metadata: dict | None = None,
        error: str = "",
    ) -> None:
        """One model or vendor call. `usage` is {"input": n, "output": n,
        "unit": "TOKENS" | "CHARACTERS" | "SECONDS" | "IMAGES"}."""

        if not self.enabled:
            return
        body = {
            "id": str(uuid.uuid4()),
            "traceId": self.trace_id,
            "name": name,
            "model": model,
            "startTime": started_at.isoformat(),
            "endTime": _now(),
            "input": _short(input),
            "output": _short(output),
            "metadata": metadata or {},
            "level": "ERROR" if error else "DEFAULT",
            "statusMessage": error[:500],
        }
        if usage:
            body["usage"] = usage
        if cost is not None:
            body["costDetails"] = {"total": float(cost)}
        self._add("generation-create", body)

    def score(self, *, name: str, value: float, comment: str = "") -> None:
        if not self.enabled:
            return
        self._add(
            "score-create",
            {"id": str(uuid.uuid4()), "traceId": self.trace_id, "name": name, "value": float(value), "comment": comment[:500]},
        )

    def flush(self) -> None:
        if not self.enabled or not self.events:
            return
        batch, self.events = self.events, []
        try:
            response = httpx.post(
                f"{settings.LANGFUSE_HOST.rstrip('/')}/api/public/ingestion",
                json={"batch": batch},
                auth=(settings.LANGFUSE_PUBLIC_KEY, settings.LANGFUSE_SECRET_KEY),
                timeout=FLUSH_TIMEOUT_SECONDS,
            )
            if response.status_code >= 400:
                logger.warning("Langfuse refused %s events: HTTP %s", len(batch), response.status_code)
        except httpx.HTTPError as exc:
            logger.warning("Langfuse unreachable, %s events dropped: %s", len(batch), exc)

"""
SISSL HTTP provider — the ONLY module in the codebase that talks to
https://api.sissl.tech directly.

Why this strict isolation?

  - Retries / timeouts / auth headers live in one place. Service code
    never has to know about HTTP-level concerns.
  - Swapping vendors (or stubbing for tests) only touches this file.
  - PII never leaks into logs — the log lines below format the operation
    KIND, not the body.

This file deliberately keeps a narrow surface: one method per upstream
endpoint, all returning parsed JSON dicts. Higher-level concerns (logging
rows, applying thresholds, raising domain exceptions, rate-limiting) belong
in services/sissl_service.py.
"""
from __future__ import annotations

import logging
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from api.sissl_verification.exceptions import SISSLError
from shared.constants.kyc import (
    SISSL_API_TOKEN,
    SISSL_BASE_URL,
    SISSL_CONNECT_TIMEOUT,
    SISSL_HTTP_TIMEOUT,
    SISSL_MAX_RETRIES,
)

logger = logging.getLogger(__name__)


# >>>>>>>>>>>>>>>>>>>>>>> Session Builder <<<<<<<<<<<<<<<<<<<<<<<<<<<<
def _build_session() -> requests.Session:
    """
    Builds a requests.Session with tightly-bounded retries.

    This call runs synchronously inside a request served behind a ~100s gateway
    timeout (Cloudflare / App Platform), so total wall time is a hard budget —
    a request that overruns it comes back to the client as a raw 504, not our
    clean 503. Every retry spends that budget, so we only retry the cases that
    fail FAST and are genuinely transient:

      - `status_forcelist=(502, 503)` — a fast "vendor briefly unavailable"
        response IS worth one more shot. 504 is deliberately excluded: a vendor
        gateway timeout means NIMC/NIBSS behind SISSL is slow, and retrying just
        waits out another full timeout for the same slow upstream.
      - `read=0` — a read timeout is NOT a blip; it means the upstream is slow.
        Retrying it would multiply SISSL_HTTP_TIMEOUT by the attempt count (the
        exact cause of the 504s). So we fail fast on the first read timeout and
        surface a 503 to the user instead of hanging.

    We never retry 400/401/403/404 — those mean bad input (e.g. a malformed
    BVN) and a retry just burns billable vendor budget.

    `backoff_factor=0.5` -> waits 0.5s, 1.0s between attempts.
    """
    session = requests.Session()

    retry = Retry(
        total=SISSL_MAX_RETRIES,
        read=0,
        backoff_factor=0.5,
        status_forcelist=(502, 503),
        allowed_methods=("POST",),
        raise_on_status=False,
    )

    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    return session


# >>>>>>>>>>>>>>>>>>>>>>> SISSL Provider <<<<<<<<<<<<<<<<<<<<<<<<<<<<<
class SisslProvider:
    """
    One method per upstream endpoint.

    Constructor accepts optional overrides so tests can:
      - pass a mock session without monkey-patching requests
      - point at a staging URL without env-var gymnastics
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_token: str | None = None,
        session: requests.Session | None = None,
    ):
        self.base_url = base_url if base_url is not None else SISSL_BASE_URL
        self.api_token = api_token if api_token is not None else SISSL_API_TOKEN
        self._session = session or _build_session()


    # >>>>>>>>>>>>>>>>>>>> Endpoint Methods <<<<<<<<<<<<<<<<<<<<<<<<<<<<
    def liveness(self, payload: dict[str, Any]) -> dict[str, Any]:
        """
        POST /api/verification/liveness

        Body:   { "photo": <url or base64> }
        Returns { "result": "real" | "fake", "score": 0 - 100 }
        """
        return self._post(
            "/api/verification/liveness",
            {"photo": payload["photo"]},
            kind="liveness",
        )

    def bvn_verification(self, payload: dict[str, Any]) -> dict[str, Any]:
        """
        POST /api/verification/bvn

        Body:   { "id": <11-digit bvn> }

        SISSL wraps the BVN response in `{ "data": { ... } }` (but not the
        other endpoints). We unwrap here so callers always see a flat dict.
        """
        body = self._post(
            "/api/verification/bvn",
            {"id": payload["id"]},
            kind="bvn",
        )

        # Unwrap the {data: {...}} envelope if present
        if isinstance(body, dict) and "data" in body and isinstance(body["data"], dict):
            return body["data"]
        return body

    def nin_verification(self, payload: dict[str, Any]) -> dict[str, Any]:
        """
        POST /api/verification/nin

        Body:   { "id": <11-digit nin> }
        Returns a flat dict (no `data` envelope).
        """
        return self._post(
            "/api/verification/nin",
            {"id": payload["id"]},
            kind="nin",
        )


    # >>>>>>>>>>>>>>>>>>>> Internal HTTP <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
    def _post(self, path: str, body: dict[str, Any], *, kind: str) -> dict[str, Any]:
        """
        Internal helper — does the POST + error translation for every endpoint.

        Translates EVERY non-2xx (and every transport error) into SISSLError
        so callers don't have to know about `requests` exceptions.

        Special case: when SISSL returns an HTML page (Cloudflare / nginx
        outage page), we substitute a friendly user-facing message rather
        than leak raw HTML.
        """
        # [1] Bail early if we have no credentials — fail loudly rather than
        # send a malformed request that would burn a billable retry slot.
        if not self.base_url or not self.api_token:
            raise SISSLError(
                "SISSL is not configured. Set SISSL_BASE_URL and SISSL_API_TOKEN."
            )

        url = f"{self.base_url.rstrip('/')}{path}"
        logger.info(f"[<>SISSLProvider<>] POST {kind}")  # NEVER log body — it has PII

        # [2] Make the call — translate transport errors to SISSLError
        try:
            response = self._session.post(
                url,
                json=body,
                headers={
                    "Authorization": f"Bearer {self.api_token}",
                    "Content-Type": "application/json",
                },
                timeout=(SISSL_CONNECT_TIMEOUT, SISSL_HTTP_TIMEOUT),
            )
        except requests.Timeout as exc:
            logger.error(f"[<!>SISSLProvider<!>] timeout on {kind}: {exc}")
            raise SISSLError(f"SISSL {kind} timed out.") from exc
        except requests.RequestException as exc:
            logger.error(f"[<!>SISSLProvider<!>] request error on {kind}: {exc}")
            raise SISSLError(f"SISSL {kind} request failed.") from exc

        # [3] Non-2xx -> SISSLError. Detect HTML pages (vendor outages) and
        # substitute a friendly message rather than leak raw HTML.
        if not 200 <= response.status_code < 300:
            content_type = response.headers.get("Content-Type", "")
            if "text/html" in content_type or response.text.strip().startswith("<"):
                message = "Verification service is temporarily unavailable. Please try again."
            else:
                try:
                    message = response.json().get("message") or response.text
                except ValueError:
                    message = f"SISSL {kind} returned HTTP {response.status_code}"

            logger.warning(
                f"[<!>SISSLProvider<!>] non-2xx on {kind}: {response.status_code} | {message}"
            )
            raise SISSLError(message)

        # [4] Parse JSON or fail loudly
        try:
            return response.json()
        except ValueError as exc:
            logger.error(f"[<!>SISSLProvider<!>] non-JSON body on {kind}: {response.text[:200]!r}")
            raise SISSLError(f"SISSL {kind} returned a non-JSON response.") from exc

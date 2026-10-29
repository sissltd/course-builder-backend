"""Short-lived signed tokens for sharing an unpublished course preview.

A creator needs to show work in progress before it is published, and
sometimes to someone who is not signed in. A token scoped to one course
and expiring in minutes is safer than a visibility flag on the course
itself: it grants nothing but this one course, it cannot be forgotten in
the "on" position, and revocation is automatic.

The token is a JWT signed with the platform's existing SIMPLE_JWT key, so
no new secret is introduced. It carries no user identity - it authorises
reading one course, nothing else, and is deliberately useless anywhere
outside the preview resolver below.
"""

from datetime import datetime, timedelta, timezone as dt_timezone
from uuid import uuid4

import jwt as pyjwt
from django.conf import settings

TOKEN_TYPE = "course_preview"
PREVIEW_TTL = timedelta(minutes=15)
"""Long enough to open and share a link, short enough that a leaked URL
stops working before it can circulate."""


class PreviewTokenInvalid(Exception):
    """Raised when a preview token cannot be trusted."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _signing_key() -> str:
    return settings.SIMPLE_JWT["SIGNING_KEY"]


def issue_preview_token(*, course) -> tuple[str, datetime]:
    """Mint a token authorising preview of exactly `course`.

    Returns (token, expires_at) so the caller can tell the client when the
    link dies rather than leaving it to discover a 401.
    """

    now = datetime.now(dt_timezone.utc)
    expires_at = now + PREVIEW_TTL
    payload = {
        "course_id": str(course.id),
        "typ": TOKEN_TYPE,
        "iat": now,
        "exp": expires_at,
        "jti": uuid4().hex,
    }
    return pyjwt.encode(payload, _signing_key(), algorithm="HS256"), expires_at


def resolve_preview_token(token: str) -> str:
    """Validate a preview token and return the course id it authorises.

    Raises PreviewTokenInvalid with a stable machine code for every
    failure mode, so callers can distinguish "expired, ask for a fresh
    link" from "this was never valid".
    """

    try:
        payload = pyjwt.decode(
            token,
            _signing_key(),
            algorithms=["HS256"],
            options={"require": ["exp", "iat", "course_id", "typ"]},
        )
    except pyjwt.ExpiredSignatureError as exc:
        raise PreviewTokenInvalid(
            "preview_expired", "This preview link has expired; request a new one."
        ) from exc
    except pyjwt.InvalidTokenError as exc:
        raise PreviewTokenInvalid("invalid_preview", "Invalid preview link.") from exc

    # A platform access token is also signed with this key; the type claim
    # is what stops one being replayed as a preview grant.
    if payload.get("typ") != TOKEN_TYPE:
        raise PreviewTokenInvalid("invalid_preview", "Invalid preview link.")

    return payload["course_id"]

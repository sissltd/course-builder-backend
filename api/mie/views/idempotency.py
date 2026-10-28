"""HTTP side of the optional `Idempotency-Key` header on developer POSTs.

The rules live in idempotency_service; this module reads the header, replays
a stored response, and keeps a successful response for the next retry. The
OpenAPI pieces are shared so every idempotent endpoint documents the header
and its errors the same way.
"""

from collections.abc import Callable

from drf_spectacular.utils import OpenApiExample, OpenApiParameter
from rest_framework.response import Response

from api.mie.services import idempotency_service
from shared.spectacular.responses import inline_error_response

IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"
REPLAYED_HEADER = "Idempotent-Replayed"
"""Set to `true` on a response replayed from an earlier request."""

IDEMPOTENCY_KEY_PARAMETER = OpenApiParameter(
    name=IDEMPOTENCY_KEY_HEADER,
    type=str,
    location=OpenApiParameter.HEADER,
    required=False,
    description=(
        "Optional. A unique value per request (a UUID works; letters, digits, "
        "'-', '_', '.' and ':', up to 255 characters). Retrying with the same "
        "key and the same body within 24 hours returns the first successful "
        "response, with `Idempotent-Replayed: true`, instead of doing the "
        "work again. A failed request does not use up its key."
    ),
)

IDEMPOTENCY_CONFLICT_EXAMPLES = [
    OpenApiExample(
        "Idempotency-Key reused",
        value={
            "errors": [
                {
                    "type": "client_error",
                    "code": "idempotency_key_reused",
                    "message": (
                        "This Idempotency-Key was already used for a different "
                        "request. Use a new key for a new request."
                    ),
                    "field_name": None,
                }
            ]
        },
    ),
    OpenApiExample(
        "Request with this key still running",
        value={
            "errors": [
                {
                    "type": "client_error",
                    "code": "idempotency_request_in_progress",
                    "message": (
                        "A request with this Idempotency-Key is still being "
                        "processed. Retry shortly to receive its result."
                    ),
                    "field_name": None,
                }
            ]
        },
    ),
]

INVALID_KEY_EXAMPLE = OpenApiExample(
    "Invalid Idempotency-Key",
    value={
        "errors": [
            {
                "type": "validation_error",
                "code": "invalid",
                "message": (
                    "Send 1 to 255 characters: letters, digits, '-', '_', '.' "
                    "or ':'. A UUID works."
                ),
                "field_name": IDEMPOTENCY_KEY_HEADER,
            }
        ]
    },
)


def idempotency_conflict_response(*, description: str, examples: list | None = None):
    """The 409 entry for an idempotent endpoint: its own conflicts, if any,
    plus the two Idempotency-Key conflicts."""

    return inline_error_response(
        description=description,
        examples=[*(examples or []), *IDEMPOTENCY_CONFLICT_EXAMPLES],
    )


def respond_idempotently(request, handle: Callable[[], Response]) -> Response:
    """Run `handle` once per Idempotency-Key; without the header, just run it.

    A 2xx response is stored for the key and replayed to later retries. Any
    other outcome - an error response or a raised exception - releases the
    key so the same key can be retried.
    """

    key = request.headers.get(IDEMPOTENCY_KEY_HEADER)
    if key is None:
        return handle()

    claim = idempotency_service.claim(
        developer=request.auth,
        key=idempotency_service.validate_key(key),
        scope=f"{request.method} {request.path}",
        request_hash=idempotency_service.fingerprint(request.data),
    )
    if claim.replay:
        response = Response(
            claim.record.response_body, status=claim.record.response_status
        )
        response[REPLAYED_HEADER] = "true"
        return response

    try:
        response = handle()
    except BaseException:
        idempotency_service.release(record=claim.record)
        raise
    if 200 <= response.status_code < 300:
        idempotency_service.complete(
            record=claim.record, status_code=response.status_code, body=response.data
        )
    else:
        idempotency_service.release(record=claim.record)
    return response

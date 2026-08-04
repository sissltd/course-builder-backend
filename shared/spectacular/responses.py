"""
Reusable OpenAPI response definitions for drf-spectacular.

Every endpoint in Feexeet documents not just its success response but also
every error it can emit. Without a shared library each view would re-declare
the same 401/403/422/500 blocks with subtly different wording, and the
frontend team would have to learn five different ways of saying "your token
is invalid". This module gives every endpoint the SAME error contract.

Usage:

    from shared.spectacular.responses import STANDARD_ERROR_RESPONSES

    @extend_schema(
        ...,
        responses={
            201: OpenApiResponse(...),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["validation"],
            **STANDARD_ERROR_RESPONSES["server"],
        },
    )

Each key in STANDARD_ERROR_RESPONSES maps to a single-entry dict keyed by
HTTP status code, so it slots straight into the `responses=` kwarg via `**`
unpacking. Only include the errors a given view can actually emit — do not
pad responses with errors that can't happen.
"""

from drf_spectacular.utils import OpenApiExample, OpenApiResponse, inline_serializer
from rest_framework import serializers

# >>>>>>>>>>>>>>>>>>>>>>>>>> Error envelope shape <<<<<<<<<<<<<<<<<<<<<<
# Mirrors `shared/response/error.py::custom_error_response`. Keep these
# examples in sync if that envelope ever changes.
#
# IMPORTANT: drf-spectacular only renders `examples=` on an OpenApiResponse
# when `response=` is also set. Without a typed response schema there is no
# `content.application/json` block in the rendered OpenAPI doc for the
# example to attach to — and Swagger UI silently shows only the
# description. Every block in STANDARD_ERROR_RESPONSES therefore declares
# `response=ErrorEnvelopeSerializer` (or `ValidationErrorEnvelopeSerializer`
# for the 422) so the per-block examples actually surface in the FE doc.


# >>>>>>>>>>>>>>>>>>>>>>>>>> Envelope serializers <<<<<<<<<<<<<<<<<<<<<<
# These define the response SCHEMA the FE renders against. They are
# inline (no model) because the error envelope isn't a model — it's the
# shape returned by shared/response/error.py.

ErrorEnvelopeSerializer = inline_serializer(
    name="ErrorEnvelope",
    fields={
        "success": serializers.BooleanField(default=False),
        "status": serializers.IntegerField(),
        "message": serializers.CharField(),
        "technical_message": serializers.CharField(allow_null=True, required=False),
    },
)

ValidationErrorEnvelopeSerializer = inline_serializer(
    name="ValidationErrorEnvelope",
    fields={
        "success": serializers.BooleanField(default=False),
        "status": serializers.IntegerField(),
        "message": serializers.CharField(),
        "technical_message": serializers.CharField(allow_null=True, required=False),
        # `data` is a per-field error map: { "<field_name>": ["<error msg>", ...] }
        "data": serializers.DictField(
            child=serializers.ListField(child=serializers.CharField()),
        ),
    },
)


def _error_example(name: str, status: int, message: str) -> OpenApiExample:
    return OpenApiExample(
        name=name,
        value={
            "success": False,
            "status": status,
            "message": message,
            "technical_message": None,
        },
    )


def inline_error_response(*, description: str, examples: list) -> OpenApiResponse:
    """
    Build an `OpenApiResponse` for a per-endpoint inline error (typically
    a 400 / 403 / 404 / 409 / 410 / 429 that isn't covered by the generic
    STANDARD_ERROR_RESPONSES blocks).

    Bundles `response=ErrorEnvelopeSerializer` automatically — without it
    drf-spectacular silently drops the `examples=` block from the
    rendered OpenAPI doc, leaving Swagger UI showing only the
    description with no concrete example body. This helper exists so
    every per-endpoint error is rendered as richly as the
    STANDARD_ERROR_RESPONSES blocks.

    Usage:

        from shared.spectacular.responses import inline_error_response

        responses={
            201: OpenApiResponse(response=MyCreateSerializer, ...),
            409: inline_error_response(
                description="The resource already exists.",
                examples=[OpenApiExample(name="Duplicate", value={...})],
            ),
            **STANDARD_ERROR_RESPONSES["auth"],
            **STANDARD_ERROR_RESPONSES["server"],
        }
    """
    return OpenApiResponse(
        response=ErrorEnvelopeSerializer,
        description=description,
        examples=examples,
    )


# >>>>>>>>>>>>>>>>>>>>>>>>>> Standard error blocks <<<<<<<<<<<<<<<<<<<<<<

STANDARD_ERROR_RESPONSES = {
    # ---- 401 ----------------------------------------------------------
    # Use on every authenticated endpoint. Covers missing, malformed,
    # expired, or signature-invalid bearer tokens.
    "auth": {
        401: OpenApiResponse(
            response=ErrorEnvelopeSerializer,
            description=(
                "Authentication failed. The request did not include a "
                "valid Bearer token, the token has expired, or the "
                "signature does not verify."
            ),
            examples=[
                _error_example(
                    name="Missing or invalid token",
                    status=401,
                    message="Authentication credentials were not provided.",
                ),
                _error_example(
                    name="Expired token",
                    status=401,
                    message="Token has expired. Please log in again.",
                ),
            ],
        ),
    },

    # ---- 403 ----------------------------------------------------------
    # Use when the user IS authenticated but their role/permission does
    # not allow the action. Different from 401 — the credentials are
    # fine, the user just isn't allowed.
    "forbidden": {
        403: OpenApiResponse(
            response=ErrorEnvelopeSerializer,
            description=(
                "The user is authenticated but does not have permission "
                "to perform this action — typically a role or "
                "KYC-status mismatch."
            ),
            examples=[
                _error_example(
                    name="Wrong role",
                    status=403,
                    message="You do not have permission to perform this action.",
                ),
                _error_example(
                    name="KYC required",
                    status=403,
                    message="Your account must be KYC-verified before trading.",
                ),
            ],
        ),
    },

    # ---- 404 ----------------------------------------------------------
    # Use on any endpoint that looks up a resource by ID or slug.
    "not_found": {
        404: OpenApiResponse(
            response=ErrorEnvelopeSerializer,
            description="The requested resource does not exist or has been soft-deleted.",
            examples=[
                _error_example(
                    name="Not found",
                    status=404,
                    message="Resource not found.",
                ),
            ],
        ),
    },

    # ---- 409 ----------------------------------------------------------
    # Use on endpoints that can fail because of state — duplicate
    # creation, already-onboarded, double-spend, etc.
    "conflict": {
        409: OpenApiResponse(
            response=ErrorEnvelopeSerializer,
            description=(
                "The request conflicts with the current state of the "
                "resource — typically a duplicate or an attempt to "
                "perform an action that has already been completed."
            ),
            examples=[
                _error_example(
                    name="Already exists",
                    status=409,
                    message="A resource with this identifier already exists.",
                ),
            ],
        ),
    },

    # ---- 422 ----------------------------------------------------------
    # Use on every endpoint that accepts a request body. Covers all
    # serializer ValidationErrors — missing required fields, wrong
    # type, failed field-level validators.
    "validation": {
        422: OpenApiResponse(
            response=ValidationErrorEnvelopeSerializer,
            description=(
                "Validation failed. One or more fields in the request "
                "body are missing, malformed, or violate a field-level "
                "validator. The `data` object lists each failing field "
                "with an array of human-readable error strings."
            ),
            examples=[
                OpenApiExample(
                    name="Field-level validation error",
                    value={
                        "success": False,
                        "status": 422,
                        "message": "Validation failed.",
                        "technical_message": None,
                        "data": {
                            "phone_number": [
                                "Phone number must be in international format: e.g., +23412345678",
                            ],
                            "password": [
                                "Password must contain at least one uppercase letter.",
                            ],
                        },
                    },
                ),
            ],
        ),
    },

    # ---- 429 ----------------------------------------------------------
    # Use on endpoints that are rate-limited (OTP request, liveness
    # checks, login attempts).
    "rate_limited": {
        429: OpenApiResponse(
            response=ErrorEnvelopeSerializer,
            description=(
                "Too many requests in a short window. The client "
                "should back off and retry after the cooldown."
            ),
            examples=[
                _error_example(
                    name="Rate limited",
                    status=429,
                    message="Too many requests. Please wait before trying again.",
                ),
            ],
        ),
    },

    # ---- 500 ----------------------------------------------------------
    # Use on every endpoint — anything can blow up.
    "server": {
        500: OpenApiResponse(
            response=ErrorEnvelopeSerializer,
            description=(
                "An unexpected server error occurred. Safe to retry "
                "idempotent requests; non-idempotent ones (like "
                "create endpoints) may have partially succeeded — "
                "check the resource state before retrying."
            ),
            examples=[
                _error_example(
                    name="Server error",
                    status=500,
                    message="Something went wrong. Please try again.",
                ),
            ],
        ),
    },
}

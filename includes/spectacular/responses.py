"""Reusable OpenAPI error responses for the Swagger documentation standard.

The Feexeet swagger standard tells engineers to pull boilerplate errors from a
shared module rather than rewriting them on all 50 endpoints. That module is
this one, adapted to the error envelope this project actually emits.

This project does NOT use the `{"success", "status", "message"}` envelope from
the original standard. Errors here are rendered by
`includes.helpers.TsesExceptionFormatter` on top of drf-standardized-errors,
which produces:

    {"errors": [{"type": ..., "code": ..., "message": ..., "field_name": ...}]}

Every example below matches that real shape, so what a frontend engineer reads
in Swagger is what the API actually returns. Note the validation bucket is
**400**, not 422 - drf-standardized-errors preserves DRF's original status code.

Usage:

    from includes.spectacular.responses import STANDARD_ERROR_RESPONSES

    responses={
        201: OpenApiResponse(...),
        **STANDARD_ERROR_RESPONSES["validation"],   # 400
        **STANDARD_ERROR_RESPONSES["auth"],         # 401
        **STANDARD_ERROR_RESPONSES["permission"],   # 403
    }

Only spread the buckets a view can actually emit - the standard explicitly
forbids documenting errors that cannot happen (an AllowAny view has no 401).
"""

from drf_spectacular.utils import OpenApiExample, OpenApiResponse


def _error(*, type_, code, message, field_name=None):
    """Build one entry of the `errors` list in the project error envelope."""

    return {
        "type": type_,
        "code": code,
        "message": message,
        "field_name": field_name,
    }


STANDARD_ERROR_RESPONSES = {
    "validation": {
        400: OpenApiResponse(
            description=(
                "Request body failed validation. `errors` contains one entry "
                "per offending field; `field_name` identifies which."
            ),
            examples=[
                OpenApiExample(
                    name="Missing required field",
                    value={
                        "errors": [
                            _error(
                                type_="validation_error",
                                code="required",
                                message="This field is required.",
                                field_name="email",
                            )
                        ]
                    },
                ),
                OpenApiExample(
                    name="Weak password",
                    value={
                        "errors": [
                            _error(
                                type_="validation_error",
                                code="password_too_short",
                                message=(
                                    "This password is too short. It must "
                                    "contain at least 8 characters."
                                ),
                                field_name="password",
                            )
                        ]
                    },
                ),
            ],
        ),
    },
    "auth": {
        401: OpenApiResponse(
            description=(
                "No credentials were supplied, or the access token is expired "
                "or malformed. Refresh via `/api/v1/auth/token/refresh/`."
            ),
            examples=[
                OpenApiExample(
                    name="Missing credentials",
                    value={
                        "errors": [
                            _error(
                                type_="client_error",
                                code="not_authenticated",
                                message=(
                                    "Authentication credentials were not provided."
                                ),
                            )
                        ]
                    },
                ),
                OpenApiExample(
                    name="Expired token",
                    value={
                        "errors": [
                            _error(
                                type_="client_error",
                                code="token_not_valid",
                                message="Given token not valid for any token type.",
                            )
                        ]
                    },
                ),
            ],
        ),
    },
    "permission": {
        403: OpenApiResponse(
            description=(
                "The caller is authenticated but their role does not permit "
                "this operation."
            ),
            examples=[
                OpenApiExample(
                    name="Wrong role",
                    value={
                        "errors": [
                            _error(
                                type_="client_error",
                                code="permission_denied",
                                message=(
                                    "You do not have permission to perform this action."
                                ),
                            )
                        ]
                    },
                ),
            ],
        ),
    },
    "not_found": {
        404: OpenApiResponse(
            description="The requested resource does not exist.",
            examples=[
                OpenApiExample(
                    name="Not found",
                    value={
                        "errors": [
                            _error(
                                type_="client_error",
                                code="not_found",
                                message="No user found with this email.",
                            )
                        ]
                    },
                ),
            ],
        ),
    },
    "server": {
        500: OpenApiResponse(
            description=(
                "Unhandled server error. Safe to retry idempotent requests; "
                "report the correlation ID from the response headers if it "
                "persists."
            ),
            examples=[
                OpenApiExample(
                    name="Server error",
                    value={
                        "errors": [
                            _error(
                                type_="server_error",
                                code="error",
                                message="A server error occurred.",
                            )
                        ]
                    },
                ),
            ],
        ),
    },
}

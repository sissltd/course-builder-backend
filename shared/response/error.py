from rest_framework.response import Response


def _error_for_status(status, message):
    error_map = {
        400: ("validation_error", "invalid"),
        401: ("client_error", "not_authenticated"),
        403: ("client_error", "permission_denied"),
        404: ("client_error", "not_found"),
        500: ("server_error", "error"),
        # 5xx must never fall through to the client_error default below —
        # a client branching on `type` would blame the caller for an upstream
        # or server-side fault and, worse, show them a "fix your request"
        # message for something they cannot act on.
        502: ("server_error", "bad_gateway"),
        503: ("server_error", "service_unavailable"),
        504: ("server_error", "gateway_timeout"),
    }
    default = ("server_error", "error") if status >= 500 else ("client_error", "error")
    type_, code = error_map.get(status, default)
    return {
        "type": type_,
        "code": code,
        "message": message,
        "field_name": None,
    }


def custom_error_response(status, message, data=None, technical_message=None):
    response = {"errors": [_error_for_status(status, message)]}
    return Response(response, status=status)

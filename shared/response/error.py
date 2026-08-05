from rest_framework.response import Response


def _error_for_status(status, message):
    error_map = {
        400: ("validation_error", "invalid"),
        401: ("client_error", "not_authenticated"),
        403: ("client_error", "permission_denied"),
        404: ("client_error", "not_found"),
        500: ("server_error", "error"),
    }
    type_, code = error_map.get(status, ("client_error", "error"))
    return {
        "type": type_,
        "code": code,
        "message": message,
        "field_name": None,
    }


def custom_error_response(status, message, data=None, technical_message=None):
    response = {"errors": [_error_for_status(status, message)]}
    return Response(response, status=status)

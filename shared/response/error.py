from rest_framework.response import Response


def custom_error_response(status, message, data=None, technical_message=None):
    response = {
        "message": message,
        "technical_message": technical_message,
        "status": status,
        "success": False,
    }

    if data is not None:
        response["data"] = data
    return Response(response, status=status)

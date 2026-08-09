from rest_framework import status as http_status
from rest_framework.response import Response


def custom_success_response(
    status, message, data=None, errors=None, technical_message=None
):
    """
    Custom response function that includes device authorization status.
    """

    response = {
        "status": status or http_status.HTTP_200_OK,
        "success": True,
        "message": message,
    }

    if data is not None:
        response["data"] = data

    if technical_message is not None:
        response["technical_message"] = technical_message

    return Response(response, status=status)

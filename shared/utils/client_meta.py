from rest_framework.request import Request


def client_meta(request: Request) -> tuple[str, str]:
    """Returns the IP address and User agent for the request"""
    return request.META.get("REMOTE_ADDR"), request.META.get("HTTP_USER_AGENT", "")

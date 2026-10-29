from rest_framework.request import Request


def client_meta(request: Request) -> tuple[str, str]:
    """
    Extracts the real client IP address.

    X-Forwarded-For is set by reverse proxies (Nginx, Heroku, AWS ALB)
    in the format: "client, proxy1, proxy2"

    We take the leftmost value — that's the original client.
    Fallback to REMOTE_ADDR if the header isn't present (local dev).
    """
    user_agent = request.META.get("HTTP_USER_AGENT", "")
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded_for:
        ip_address = forwarded_for.split(",")[0].strip()
    else:
        ip_address = request.META.get("REMOTE_ADDR", "unknown")
    return ip_address, user_agent
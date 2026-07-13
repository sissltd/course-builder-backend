from urllib.parse import urlencode

from django.conf import settings


def build_verification_link(*, path: str, email: str, token: str) -> str:
    """Build a frontend verification/reset link: {FRONTEND_URL}{path}?email=...&token=..."""

    query = urlencode({"email": email, "token": token})
    return f"{settings.FRONTEND_URL}{path}?{query}"

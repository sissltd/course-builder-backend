from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken


class OptionalJWTAuthentication(JWTAuthentication):
    """JWT authentication for public endpoints that use the caller's identity
    when they have one.

    A valid Bearer token authenticates as usual. A missing, expired or
    malformed token leaves the request anonymous instead of failing it, so a
    stale token (e.g. one Swagger UI keeps attaching) can never 401 a public
    route - see api/authentication/tests/test_public_endpoint_auth.py.
    """

    def authenticate(self, request):
        try:
            return super().authenticate(request)
        except (InvalidToken, AuthenticationFailed):
            return None

    def authenticate_header(self, request):
        # No challenge: DRF then reports a later permission failure as 403,
        # never as a 401 asking for credentials the endpoint doesn't need.
        return None

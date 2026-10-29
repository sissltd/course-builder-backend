from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed

from api.mie.services import dev_token_service
from api.mie.services.key_service import ApiKeyRejected, authenticate_key

API_KEY_HEADER = "X-MIE-Api-Key"


class MieDeveloperAuthentication(BaseAuthentication):
    """Dual resolver for every developer-facing mie route.

    Two credential kinds are accepted, in priority order:

    1. ``X-MIE-Api-Key: scb_live_...`` - the issued API key.
    2. ``Authorization: Bearer <token>`` - a short-lived session token
       minted after platform (OTP) sign-in; FE users hit the same views.

    On success the resolved DeveloperAccount is stored as ``request.auth``
    (the principal all scoping keys off) and ``request.mie_auth_method``
    records which path matched. ``request.user`` stays anonymous on both
    paths: external developers have no rows in the platform user table,
    and view permissions gate on request.auth.
    """

    def authenticate(self, request):
        api_key = request.headers.get(API_KEY_HEADER)
        if api_key:
            try:
                account = authenticate_key(api_key)
            except ApiKeyRejected as exc:
                raise AuthenticationFailed(exc.detail, code=exc.code) from exc
            request.mie_auth_method = "api_key"
            return None, account

        auth = get_authorization_header(request).split()
        if auth and auth[0].lower() == b"bearer":
            try:
                account = dev_token_service.resolve_dev_token(auth[1].decode())
            except dev_token_service.DevTokenInvalid as exc:
                raise AuthenticationFailed(exc.detail, code=exc.code) from exc
            request.mie_auth_method = "platform"
            return None, account

        raise AuthenticationFailed(
            "Provide X-MIE-Api-Key or a Bearer session token.", code="no_credentials"
        )

    def authenticate_header(self, request):
        return 'Bearer realm="mie"'


def _register_schema_extension():
    """Teach drf-spectacular about this authentication class.

    Registered lazily so importing the module never requires
    drf-spectacular to be installed in tooling contexts.
    """

    from drf_spectacular.extensions import OpenApiAuthenticationExtension

    class MieDeveloperAuthScheme(OpenApiAuthenticationExtension):
        target_class = f"{__name__}.MieDeveloperAuthentication"
        name = "mieDeveloperAuth"

        def get_security_definition(self, auto_schema):
            return {
                "type": "apiKey",
                "in": "header",
                "name": API_KEY_HEADER,
                "description": (
                    "Developer credentials: send the full scb_live_ key in "
                    "X-MIE-Api-Key, OR a platform session token as "
                    "'Authorization: Bearer <token>'."
                ),
            }

    return MieDeveloperAuthScheme


_register_schema_extension()

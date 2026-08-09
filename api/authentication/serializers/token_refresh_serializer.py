from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.serializers import (
    TokenRefreshSerializer as SimpleJWTTokenRefreshSerializer,
)
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import RefreshToken

from api.authentication.services import session_service
from api.users.enums import AccountStatus
from api.users.models import User


class TokenRefreshSerializer(SimpleJWTTokenRefreshSerializer):
    """Same as SimpleJWT's TokenRefreshSerializer, but rejects the refresh if
    the token's owning user is no longer active - stock SimpleJWT only
    checks the token itself (signature/expiry/blacklist), never the live
    user row, so a suspended/deactivated user's still-valid refresh token
    would otherwise keep minting new access tokens.
    """

    def validate(self, attrs):
        # Decode first (raises TokenError -> InvalidToken for a malformed/
        # expired/already-blacklisted token, same failure mode as the base
        # class) so we can check the owning user's live status BEFORE any
        # rotation happens - rotating first and rejecting after would have
        # already handed out a valid new token pair.
        try:
            token = RefreshToken(attrs["refresh"])
        except TokenError as exc:
            raise InvalidToken(exc.args[0]) from exc

        user = User.objects.filter(pk=token.get(api_settings.USER_ID_CLAIM)).first()
        if (
            user is None
            or not user.is_active
            or user.status in (AccountStatus.SUSPENDED, AccountStatus.DEACTIVATED)
        ):
            raise InvalidToken("This account is not active. Please contact support.")

        # "sid" survives rotation unchanged (see UserSession's docstring), so
        # it identifies which session this refresh belongs to before we know
        # the new jti data["refresh"] will carry.
        sid = token.get("sid")
        data = super().validate(attrs)

        if sid and "refresh" in data:
            new_jti = RefreshToken(data["refresh"])["jti"]
            session_service.bump_last_seen(session_id=sid, new_jti=new_jti)

        return data

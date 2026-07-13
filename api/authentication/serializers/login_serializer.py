from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from api.authentication.services import activity_service
from api.users.enums import UserActivityActionEnums


class LoginSerializer(TokenObtainPairSerializer):
    """Adds a UserActivityLog(action=LOGIN) write on top of simplejwt's
    already-correct credential check + is_active gate (USER_AUTHENTICATION_RULE).

    No new input fields: TokenObtainPairSerializer already exposes
    User.USERNAME_FIELD ("email") + "password".
    """

    def validate(self, attrs):
        data = super().validate(attrs)
        activity_service.log_auth_activity(
            user=self.user,
            action=UserActivityActionEnums.LOGIN,
            summary="User logged in.",
            request=self.context.get("request"),
        )
        return data

from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from api.authentication.services import activity_service
from api.users.enums import UserActivityActionEnums
from api.users.models import User


class LoginSerializer(TokenObtainPairSerializer):
    """Adds a UserActivityLog(action=LOGIN) write, plus a pre-check that
    reports wrong-email/wrong-password/unverified as distinct field-scoped
    validation errors rather than simplejwt's single generic
    AuthenticationFailed - a deliberate choice to prioritize specific
    feedback over anti-enumeration.

    No new input fields: TokenObtainPairSerializer already exposes
    User.USERNAME_FIELD ("email") + "password".
    """

    def validate(self, attrs):
        user = User.objects.filter(email__iexact=attrs[self.username_field]).first()
        if user is None:
            raise serializers.ValidationError(
                {"email": "No account found with this email address."}
            )
        if not user.check_password(attrs["password"]):
            raise serializers.ValidationError({"password": "Incorrect password."})
        if not user.is_active:
            raise serializers.ValidationError(
                {
                    "email": "This account has not been verified yet. Please check your email for a verification link."
                }
            )

        data = super().validate(attrs)
        activity_service.log_auth_activity(
            user=self.user,
            action=UserActivityActionEnums.LOGIN,
            summary="User logged in.",
            request=self.context.get("request"),
        )
        return data

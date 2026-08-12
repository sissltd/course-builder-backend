from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from api.users.models.user import PHONE_NUMBER_VALIDATOR


class SignupSerializer(serializers.Serializer):
    """Signup input. `role` is deliberately absent - always forced to
    UserRole.COURSE_CREATOR server-side in AuthenticationService.signup."""

    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, validators=[validate_password])
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)
    country = serializers.CharField(max_length=2)
    phone_number = serializers.CharField(max_length=20, validators=[PHONE_NUMBER_VALIDATOR])
    terms_accepted = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs):
        return attrs

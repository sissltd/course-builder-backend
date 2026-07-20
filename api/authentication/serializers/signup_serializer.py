from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers


class SignupSerializer(serializers.Serializer):
    """Signup input. `role` is deliberately absent - always forced to
    UserRole.COURSE_CREATOR server-side in AuthenticationService.signup."""

    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, validators=[validate_password])
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)
    country = serializers.CharField(max_length=2)
    terms_accepted = serializers.BooleanField(required=False, default=False)

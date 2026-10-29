from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers


class SuperAdminBootstrapSerializer(serializers.Serializer):
    """One-time bootstrap input for claiming the platform's Super Admin seat.

    `role` is deliberately absent - always forced to UserRole.SUPER_ADMIN
    server-side in StaffService.bootstrap_superadmin, exactly as SignupSerializer
    forces COURSE_CREATOR.
    """

    email = serializers.EmailField(
        help_text=(
            "Login email for the Super Admin account. Becomes the permanent "
            "credential - this account signs in at /api/v1/auth/login/ like "
            "any other user."
        ),
    )
    password = serializers.CharField(
        write_only=True,
        validators=[validate_password],
        help_text=(
            "Password for the Super Admin account. Must pass Django's "
            "configured password validators (minimum 8 characters, not "
            "entirely numeric, not a common password)."
        ),
    )
    first_name = serializers.CharField(
        max_length=150,
        help_text="Given name of the person claiming the Super Admin seat.",
    )
    last_name = serializers.CharField(
        max_length=150,
        help_text="Family name of the person claiming the Super Admin seat.",
    )

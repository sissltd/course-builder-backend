from rest_framework import serializers


class GoogleLoginSerializer(serializers.Serializer):
    """Google ID token submitted by the web or mobile client."""

    id_token = serializers.CharField(
        write_only=True,
        trim_whitespace=False,
        help_text=(
            "Google OpenID Connect ID token returned by the platform's Google "
            "Identity SDK."
        ),
    )


class GoogleSignupSerializer(GoogleLoginSerializer):
    """Profile information required when creating a Google-backed account."""

    first_name = serializers.CharField(
        max_length=150,
        help_text="Editable first name to store on the new local account.",
    )
    last_name = serializers.CharField(
        max_length=150,
        help_text="Editable last name to store on the new local account.",
    )
    country = serializers.CharField(
        min_length=2,
        max_length=2,
        help_text="ISO 3166-1 alpha-2 country code for the new account.",
    )
    terms_accepted = serializers.BooleanField(
        help_text="Must be true to create or link an account through Google.",
    )

    def validate_terms_accepted(self, value):
        if not value:
            raise serializers.ValidationError(
                "You must accept the Terms and Conditions and Privacy Policy."
            )
        return value

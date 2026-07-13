from rest_framework import serializers

from api.authentication.enums import TokenPurpose


class ResendVerificationSerializer(serializers.Serializer):
    email = serializers.EmailField()
    purpose = serializers.ChoiceField(choices=TokenPurpose.choices)

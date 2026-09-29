from rest_framework import serializers

from api.support.models import SupportRequest
from api.support.services import support_service


class SupportRequestSerializer(serializers.ModelSerializer):
    """Read representation shared by every Support endpoint."""

    submitted_by_email = serializers.EmailField(
        source="submitted_by.email", read_only=True, default=None
    )

    class Meta:
        model = SupportRequest
        fields = [
            "id",
            "kind",
            "first_name",
            "last_name",
            "email",
            "country",
            "title",
            "web_link",
            "message",
            "status",
            "due_at",
            "resolution_notes",
            "resolved_at",
            "submitted_by_email",
            "created_datetime",
        ]
        read_only_fields = fields


class SupportContactCreateSerializer(serializers.Serializer):
    """Figma 'Contact us' form: first/last name, email, country, message."""

    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)
    email = serializers.EmailField()
    country = serializers.RegexField(
        r"^[A-Za-z]{2}$",
        error_messages={"invalid": "Use a two-letter ISO country code."},
    )
    message = serializers.CharField()

    def validate_country(self, value: str) -> str:
        return value.upper()

    def create(self, validated_data):
        user = self.context["request"].user
        return support_service.submit_contact(
            user=user if user.is_authenticated else None, **validated_data
        )

    def to_representation(self, instance):
        return SupportRequestSerializer(instance, context=self.context).data


class SupportTitledRequestCreateSerializer(serializers.Serializer):
    """Figma 'Request for an appeal' form (also used for tickets): Title*,
    Email*, Web link, Description*. The view supplies `kind`."""

    title = serializers.CharField(max_length=255)
    email = serializers.EmailField()
    web_link = serializers.URLField(required=False, allow_blank=True, default="")
    description = serializers.CharField()

    def create(self, validated_data):
        return support_service.submit_titled_request(
            user=self.context["request"].user,
            kind=self.context["kind"],
            **validated_data,
        )

    def to_representation(self, instance):
        return SupportRequestSerializer(instance, context=self.context).data


class SupportResolveSerializer(serializers.Serializer):
    """Body of the staff resolve action - optional free-text notes."""

    notes = serializers.CharField(required=False, allow_blank=True, default="")

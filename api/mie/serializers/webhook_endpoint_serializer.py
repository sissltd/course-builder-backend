"""A developer's webhook endpoints: what they send to add or change one, and
what they get back."""

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from api.mie.enums import WEBHOOK_ALL_EVENTS, WebhookEventType
from api.mie.models import WebhookEndpoint
from api.mie.services import webhook_endpoint_service

EVENTS_HELP_TEXT = (
    f'Which events this endpoint receives: ["{WEBHOOK_ALL_EVENTS}"] for every '
    "event type, including ones added later, or one or more of "
    f"{WebhookEventType.values}."
)


class WebhookEndpointSerializer(serializers.ModelSerializer):
    """One of the developer's endpoints, as they see it."""

    events = serializers.SerializerMethodField(help_text=EVENTS_HELP_TEXT)

    class Meta:
        model = WebhookEndpoint
        fields = ("id", "url", "events", "created_datetime", "updated_datetime")
        read_only_fields = fields
        extra_kwargs = {
            "id": {"help_text": "Endpoint id, used to edit or delete it."},
            "url": {"help_text": "HTTPS URL that receives a signed POST for each event it takes."},
            "created_datetime": {"help_text": "When the endpoint was added."},
            "updated_datetime": {"help_text": "When its URL or events last changed."},
        }

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_events(self, obj) -> list[str]:
        return webhook_endpoint_service.events_for(obj)


class WebhookEndpointWriteSerializer(serializers.Serializer):
    """Body of POST /mie/v1/webhooks/ (both fields required) and of PATCH
    /mie/v1/webhooks/{id}/ (either field, at least one)."""

    url = serializers.URLField(
        required=False,
        help_text="HTTPS URL that will receive a signed POST for each event it takes.",
    )
    events = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=False,
        help_text=EVENTS_HELP_TEXT,
    )

    def validate_events(self, value):
        """["all"] alone, or known event types. Duplicates are collapsed and
        the list is kept in catalogue order."""

        chosen = set(value)
        if WEBHOOK_ALL_EVENTS in chosen:
            if len(chosen) > 1:
                raise serializers.ValidationError(
                    f'"{WEBHOOK_ALL_EVENTS}" already includes every event; send it on its own.'
                )
            return [WEBHOOK_ALL_EVENTS]
        unknown = sorted(chosen - set(WebhookEventType.values))
        if unknown:
            raise serializers.ValidationError(
                f'Unknown event types: {unknown}. Use "{WEBHOOK_ALL_EVENTS}" or any of '
                f"{WebhookEventType.values}."
            )
        return [event for event in WebhookEventType.values if event in chosen]

    def validate(self, attrs):
        if self.partial:
            if not attrs:
                raise serializers.ValidationError("Send a url, events, or both.")
            return attrs
        missing = {"url", "events"} - set(attrs)
        if missing:
            raise serializers.ValidationError(
                {field: ["This field is required."] for field in sorted(missing)}
            )
        return attrs


class WebhookEventTypeSerializer(serializers.Serializer):
    """One event type an endpoint can subscribe to."""

    event = serializers.CharField(help_text="Value to send in an endpoint's `events`.")
    label = serializers.CharField(help_text="Human-readable name.")
    fires_when = serializers.CharField(help_text="What makes it fire.")

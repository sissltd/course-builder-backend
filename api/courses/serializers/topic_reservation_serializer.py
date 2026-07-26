from rest_framework import serializers

from api.courses.models import TopicReservationRequest
from api.courses.serializers.topic_serializer import TopicSerializer
from api.courses.services import topic_reservation_service


class TopicReservationRequestSerializer(serializers.ModelSerializer):
    """Read-only representation of a TopicReservationRequest."""

    topic = TopicSerializer(read_only=True)

    class Meta:
        model = TopicReservationRequest
        fields = [
            "id",
            "topic",
            "status",
            "reviewed_at",
            "created_datetime",
        ]
        read_only_fields = fields


class TopicReservationRequestCreateSerializer(serializers.ModelSerializer):
    """Write serializer for a creator requesting a Topic reservation."""

    class Meta:
        model = TopicReservationRequest
        fields = ["topic"]

    def create(self, validated_data):
        request = self.context["request"]
        return topic_reservation_service.submit_request(
            user=request.user, topic=validated_data["topic"]
        )

    def to_representation(self, instance):
        return TopicReservationRequestSerializer(instance, context=self.context).data

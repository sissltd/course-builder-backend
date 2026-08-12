from rest_framework import serializers

from api.courses.models import TopicReservationRequest
from api.courses.serializers.course_serializer import CategoryMiniSerializer
from api.courses.serializers.topic_serializer import TopicSerializer
from api.courses.services import topic_reservation_service


class TopicReservationRequestSerializer(serializers.ModelSerializer):
    """Read-only representation of a TopicReservationRequest.

    `topic` stays null until the request is approved - the whole point of
    this flow is requesting a topic that doesn't exist yet.
    """

    category = CategoryMiniSerializer(read_only=True)
    topic = TopicSerializer(read_only=True)
    user_type = serializers.CharField(source="requested_by.role", read_only=True)

    class Meta:
        model = TopicReservationRequest
        fields = [
            "id",
            "name",
            "category",
            "topic",
            "status",
            "rejection_reason",
            "reviewed_at",
            "user_type",
            "created_datetime",
        ]
        read_only_fields = fields


class TopicReservationRequestCreateSerializer(serializers.ModelSerializer):
    """Write serializer for a creator requesting a brand-new Topic."""

    class Meta:
        model = TopicReservationRequest
        fields = ["name", "category"]

    def create(self, validated_data):
        request = self.context["request"]
        return topic_reservation_service.submit_request(
            user=request.user,
            name=validated_data["name"],
            category=validated_data["category"],
        )

    def to_representation(self, instance):
        return TopicReservationRequestSerializer(instance, context=self.context).data


class TopicReservationRejectSerializer(serializers.Serializer):
    """Request body for the reject action - an optional free-text reason,
    e.g. the proposed name already matches an existing topic."""

    reason = serializers.CharField(required=False, allow_blank=True, default="")

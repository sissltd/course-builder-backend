from rest_framework import serializers

from api.catalog.models import Topic
from api.catalog.serializers.category_serializer import CategoryMiniSerializer
from api.catalog.services import topic_service


class TopicMiniSerializer(serializers.ModelSerializer):
    """Lightweight Topic representation for nesting inside Course payloads."""

    class Meta:
        model = Topic
        fields = ["id", "name"]
        read_only_fields = fields


class TopicSerializer(serializers.ModelSerializer):
    """Read-only representation of a Topic for creators/reviewers."""

    category = CategoryMiniSerializer(read_only=True)
    is_currently_reserved = serializers.BooleanField(read_only=True)

    class Meta:
        model = Topic
        fields = [
            "id",
            "category",
            "name",
            "creator_price",
            "status",
            "reserved_by",
            "reserved_until",
            "is_currently_reserved",
            "created_datetime",
            "updated_datetime",
        ]
        read_only_fields = fields


class TopicWriteSerializer(serializers.ModelSerializer):
    """Admin-only create/update serializer for Topic.

    Mirrors CategoryWriteSerializer exactly - creator_price's non-negativity
    is enforced by the model field's MinValueValidator, and read-only `id`
    is included so a client creating a topic gets it back without a second
    GET round-trip.
    """

    class Meta:
        model = Topic
        fields = ["id", "category", "name", "creator_price", "status"]
        read_only_fields = ["id"]

    def update(self, instance, validated_data):
        request = self.context.get("request")
        return topic_service.update_topic(
            topic=instance,
            actor=request.user if request else None,
            data=validated_data,
        )

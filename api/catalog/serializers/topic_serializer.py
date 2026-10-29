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
    """Read-only representation of a Topic for creators/reviewers. A null
    price means the topic uses its category's price of that type."""

    category = CategoryMiniSerializer(read_only=True)
    is_currently_reserved = serializers.BooleanField(read_only=True)

    class Meta:
        model = Topic
        fields = [
            "id",
            "category",
            "name",
            "creator_price_without_video",
            "creator_price_with_video",
            "creator_price_ai_built",
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

    The three price fields override the category's prices for this topic;
    null (or omitted) uses the category's. Their non-negativity is enforced
    by the model fields' MinValueValidator, and setting any of them needs
    `catalog.set_creator_prices`. Read-only `id` is included so a client
    creating a topic gets it back without a second GET round-trip.
    """

    class Meta:
        model = Topic
        fields = [
            "id",
            "category",
            "name",
            "creator_price_without_video",
            "creator_price_with_video",
            "creator_price_ai_built",
            "status",
        ]
        read_only_fields = ["id"]
        extra_kwargs = {
            "creator_price_without_video": {"help_text": "This topic's without-video price; null uses the category's."},
            "creator_price_with_video": {"help_text": "This topic's with-video price; null uses the category's."},
            "creator_price_ai_built": {"help_text": "This topic's AI-built price; null uses the category's."},
        }

    def create(self, validated_data):
        request = self.context.get("request")
        return topic_service.create_topic(actor=request.user if request else None, data=validated_data)

    def update(self, instance, validated_data):
        request = self.context.get("request")
        return topic_service.update_topic(
            topic=instance,
            actor=request.user if request else None,
            data=validated_data,
        )

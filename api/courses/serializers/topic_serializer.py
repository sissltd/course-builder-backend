from rest_framework import serializers

from api.courses.models import Topic
from api.courses.serializers.course_serializer import CategoryMiniSerializer


class TopicSerializer(serializers.ModelSerializer):
    """Read-only representation of a Topic for creators/reviewers."""

    category = CategoryMiniSerializer(read_only=True)

    class Meta:
        model = Topic
        fields = [
            "id",
            "category",
            "name",
            "creator_price",
            "status",
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

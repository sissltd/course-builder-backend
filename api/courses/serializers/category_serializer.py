from rest_framework import serializers

from api.courses.models import Category


class CategorySerializer(serializers.ModelSerializer):
    """Read-only representation of a Category for creators/reviewers."""

    class Meta:
        model = Category
        fields = [
            "id",
            "name",
            "description",
            "creator_price",
            "track_preference",
            "status",
            "created_datetime",
            "updated_datetime",
        ]
        read_only_fields = fields


class CategoryWriteSerializer(serializers.ModelSerializer):
    """Admin-only create/update serializer for Category.

    creator_price's non-negativity is enforced by the model field's
    MinValueValidator, which ModelSerializer picks up automatically - no
    duplicate validation here. Includes read-only `id` so a client creating a
    category gets it back without a second GET round-trip.
    """

    class Meta:
        model = Category
        fields = ["id", "name", "description", "creator_price", "track_preference", "status"]
        read_only_fields = ["id"]

from drf_spectacular.utils import OpenApiExample, extend_schema_serializer
from rest_framework import serializers


class ReorderItemSerializer(serializers.Serializer):
    """One item's new position in a reordered list."""

    id = serializers.UUIDField(help_text="Id of the module or lesson being placed.")
    order = serializers.IntegerField(
        min_value=0, help_text="Its new zero-or-greater position among its siblings."
    )


@extend_schema_serializer(
    examples=[
        OpenApiExample(
            "Reversing a three-item list",
            request_only=True,
            value={
                "order": [
                    {"id": "0d1c7b2e-6f5a-4a3f-9a2b-1f4e8c9d0a11", "order": 1},
                    {"id": "1e2d3c4b-5a69-4f8e-9d0c-2b3a4e5f6a7b", "order": 2},
                    {"id": "2f3e4d5c-6b7a-4098-8e1d-3c4b5a6f7e8d", "order": 3},
                ]
            },
        )
    ]
)
class ReorderSerializer(serializers.Serializer):
    """Request body for the bulk reorder endpoints.

    The list must name every sibling, not just the ones that moved - a
    partial reorder would leave the omitted rows on stale positions that
    collide with the new ones.
    """

    order = ReorderItemSerializer(
        many=True,
        allow_empty=False,
        help_text="The complete sibling set, each with its new position.",
    )

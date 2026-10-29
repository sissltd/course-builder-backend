from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from api.reviews.models import ReviewAction, ReviewFlag


class ReviewFlagSerializer(serializers.ModelSerializer):
    """Representation of one structured review issue."""

    class Meta:
        model = ReviewFlag
        fields = [
            "id",
            "review_action",
            "lesson",
            "module",
            "flag_type",
            "title",
            "system_message",
            "reviewer_note",
            "is_resolved",
            "resolved_at",
            "created_datetime",
        ]
        read_only_fields = fields


class ReviewActionSerializer(serializers.ModelSerializer):
    """Read-only representation of a ReviewAction (audit record), with any
    structured flags raised in that review round nested inline."""

    reviewer = serializers.SerializerMethodField()
    flags = ReviewFlagSerializer(many=True, read_only=True)

    class Meta:
        model = ReviewAction
        fields = [
            "id",
            "course",
            "reviewer",
            "action",
            "feedback",
            "flags",
            "created_datetime",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.DictField)
    def get_reviewer(self, obj) -> dict | None:
        if not obj.reviewer_id:
            return None
        return {"id": obj.reviewer_id, "email": obj.reviewer.email}

from rest_framework import serializers

from api.reviews.models import CourseQualityCheck, QualityCheckCriterion


class QualityCheckCriterionSerializer(serializers.ModelSerializer):
    """Representation of one admin-configurable checklist item."""

    class Meta:
        model = QualityCheckCriterion
        fields = ["id", "section", "label", "order_index", "is_active"]
        read_only_fields = ["id"]


class CourseQualityCheckSerializer(serializers.ModelSerializer):
    """Representation of a course's result for one criterion."""

    criterion = QualityCheckCriterionSerializer(read_only=True)

    class Meta:
        model = CourseQualityCheck
        fields = [
            "id",
            "criterion",
            "is_checked",
            "warning_note",
            "checked_at",
        ]
        read_only_fields = fields

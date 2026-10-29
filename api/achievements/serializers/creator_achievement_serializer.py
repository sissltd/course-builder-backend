from rest_framework import serializers

from api.achievements.models import Badge


class AchievementBadgeSerializer(serializers.ModelSerializer):
    criterion_label = serializers.CharField(
        source="get_criterion_display",
        read_only=True,
        help_text="Display name for the criterion.",
    )
    requirement_summary = serializers.CharField(
        read_only=True, help_text="Human-readable requirement line."
    )

    class Meta:
        model = Badge
        fields = [
            "id",
            "title",
            "icon",
            "color",
            "criterion",
            "criterion_label",
            "required_count",
            "requirement_summary",
        ]
        read_only_fields = fields
        extra_kwargs = {
            "id": {"help_text": "Badge id."},
            "title": {"help_text": "Badge name."},
            "icon": {"help_text": "Icon identifier."},
            "color": {"help_text": "Colour as #RRGGBB."},
            "criterion": {"help_text": "What required_count counts."},
            "required_count": {"help_text": "Courses required to earn it."},
        }


class CreatorAchievementSerializer(serializers.Serializer):
    """One badge with the signed-in creator's progress towards it."""

    badge = AchievementBadgeSerializer(help_text="The badge.")
    earned = serializers.BooleanField(help_text="Whether the creator holds it.")
    awarded_at = serializers.DateTimeField(
        allow_null=True, help_text="When it was awarded; null if not earned."
    )
    current_count = serializers.IntegerField(
        help_text=(
            "The creator's current count for the badge's criterion. May exceed "
            "required_count, and may sit below it on an earned badge whose "
            "requirement was raised later."
        )
    )

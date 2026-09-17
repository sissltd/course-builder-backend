from rest_framework import serializers

from api.achievements.enums import AwardSource, BadgeCriterion
from api.achievements.models import Badge, CreatorBadge

HEX_COLOR_PATTERN = r"^#[0-9A-Fa-f]{6}$"


class BadgeSerializer(serializers.ModelSerializer):
    """A badge as the Achievement award list and analytics cards draw it."""

    criterion_label = serializers.CharField(
        source="get_criterion_display",
        read_only=True,
        help_text="Display name for the criterion, safe to render as-is.",
    )
    requirement_summary = serializers.CharField(
        read_only=True,
        help_text=(
            "The line under the badge title, e.g. 'For creators who have "
            "created 100 courses'. Derived from criterion and required_count."
        ),
    )
    holder_count = serializers.IntegerField(
        read_only=True,
        help_text="How many creators currently hold this badge.",
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
            "auto_award",
            "requirement_summary",
            "holder_count",
            "created_datetime",
            "updated_datetime",
        ]
        read_only_fields = fields
        extra_kwargs = {
            "id": {"help_text": "Badge id."},
            "created_datetime": {"help_text": "When the badge was created."},
            "updated_datetime": {"help_text": "When the badge was last changed."},
        }


class BadgeCreateSerializer(serializers.Serializer):
    """Body of the Add new badge dialog."""

    title = serializers.CharField(
        max_length=80,
        help_text="Badge name, unique among live badges (case-insensitive).",
    )
    icon = serializers.CharField(
        max_length=50, help_text="Icon identifier from the client's badge icon set."
    )
    color = serializers.RegexField(
        HEX_COLOR_PATTERN, help_text="Badge colour as #RRGGBB, e.g. '#F2994A'."
    )
    criterion = serializers.ChoiceField(
        choices=BadgeCriterion.choices,
        default=BadgeCriterion.COURSES_CREATED,
        help_text=(
            "What required_count counts. Defaults to COURSES_CREATED. Cannot "
            "be changed after creation."
        ),
    )
    required_count = serializers.IntegerField(
        min_value=1,
        help_text="How many courses meeting the criterion earn this badge.",
    )
    auto_award = serializers.BooleanField(
        default=False,
        help_text=(
            "Award automatically to every creator who meets the requirement, "
            "including those who already do."
        ),
    )


class BadgeUpdateSerializer(serializers.Serializer):
    """Body of the Edit and Configure dialogs. Every field is optional."""

    title = serializers.CharField(
        max_length=80, required=False, help_text="New badge name."
    )
    icon = serializers.CharField(
        max_length=50, required=False, help_text="New icon identifier."
    )
    color = serializers.RegexField(
        HEX_COLOR_PATTERN, required=False, help_text="New colour as #RRGGBB."
    )
    required_count = serializers.IntegerField(
        min_value=1,
        required=False,
        help_text=(
            "New requirement. Raising it never revokes; lowering it awards "
            "anyone who now qualifies when auto_award is on."
        ),
    )
    auto_award = serializers.BooleanField(
        required=False,
        help_text="Switching this on awards everyone who already qualifies.",
    )

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("Provide at least one field to update.")
        return attrs


class BadgeRungSerializer(serializers.ModelSerializer):
    """A compact badge reference, e.g. the badge holders would move down to."""

    class Meta:
        model = Badge
        fields = ["id", "title", "icon", "color", "required_count"]
        read_only_fields = fields
        extra_kwargs = {
            "id": {"help_text": "Badge id."},
            "title": {"help_text": "Badge name."},
            "icon": {"help_text": "Icon identifier."},
            "color": {"help_text": "Colour as #RRGGBB."},
            "required_count": {"help_text": "Courses required to earn it."},
        }


class BadgeDeletionImpactSerializer(serializers.Serializer):
    badge_id = serializers.UUIDField(help_text="The badge being deleted.")
    holder_count = serializers.IntegerField(
        help_text="Creators who would lose the badge."
    )
    previous_badge = BadgeRungSerializer(
        allow_null=True,
        help_text=(
            "The badge holders would move down to. Null means there is no "
            "lower badge on this ladder, so the move option must be disabled."
        ),
    )


class BadgeDeleteQuerySerializer(serializers.Serializer):
    move_to_previous = serializers.BooleanField(
        default=False,
        help_text="Give holders the previous badge on the ladder before removing this one.",
    )


class BadgeDeletionResultSerializer(serializers.Serializer):
    badge_id = serializers.UUIDField(help_text="The deleted badge.")
    holders_removed = serializers.IntegerField(help_text="Creators who lost the badge.")
    moved_to_badge = BadgeRungSerializer(
        allow_null=True, help_text="The badge holders were moved to, or null."
    )
    holders_moved = serializers.IntegerField(
        help_text=(
            "Creators newly given the previous badge. Holders who already had "
            "it are not counted."
        )
    )


class BadgeHolderCreatorSerializer(serializers.Serializer):
    id = serializers.UUIDField(help_text="Creator's user id.")
    email = serializers.EmailField(help_text="Creator's email.")
    full_name = serializers.CharField(
        source="get_full_name", help_text="Creator's display name."
    )
    avatar_url = serializers.CharField(help_text="Creator's avatar file key or URL.")


class BadgeHolderSerializer(serializers.ModelSerializer):
    """One creator holding a badge."""

    creator = BadgeHolderCreatorSerializer(help_text="The creator holding the badge.")
    source = serializers.ChoiceField(
        choices=AwardSource.choices, help_text="How the creator came to hold it."
    )
    awarded_by_email = serializers.EmailField(
        source="awarded_by.email",
        allow_null=True,
        default=None,
        help_text="Staff member who awarded it by hand; null when automatic.",
    )

    class Meta:
        model = CreatorBadge
        fields = ["id", "creator", "source", "awarded_by_email", "awarded_at"]
        read_only_fields = fields
        extra_kwargs = {
            "id": {"help_text": "Award id."},
            "awarded_at": {"help_text": "When the badge was awarded."},
        }


class BadgeAwardCreateSerializer(serializers.Serializer):
    creator_id = serializers.UUIDField(
        help_text="User id of the Course Creator or Writer to award the badge to."
    )

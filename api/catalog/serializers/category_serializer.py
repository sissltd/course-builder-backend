from rest_framework import serializers

from api.catalog.enums import CategoryDeletionStrategy
from api.catalog.models import Category
from api.catalog.services import category_service


class CategoryMiniSerializer(serializers.ModelSerializer):
    """Lightweight Category representation for nesting inside Course payloads."""

    class Meta:
        model = Category
        fields = ["id", "name"]
        read_only_fields = fields


class CategorySerializer(serializers.ModelSerializer):
    """Read-only representation of a Category for creators/reviewers.

    `total_courses` comes from an annotation the viewset adds, so listing
    N categories stays one query rather than N+1. It reads 0 when the
    annotation is absent (e.g. a serializer used outside that queryset).
    """

    total_courses = serializers.IntegerField(
        read_only=True,
        default=0,
        help_text="Courses filed under this category. Annotated by the list view.",
    )

    class Meta:
        model = Category
        fields = [
            "id",
            "name",
            "description",
            "creator_price_beginner",
            "creator_price_intermediate",
            "creator_price_advanced",
            "icon",
            "track_preference",
            "status",
            "total_courses",
            "created_datetime",
            "updated_datetime",
        ]
        read_only_fields = fields


class CategoryWriteSerializer(serializers.ModelSerializer):
    """Create/update serializer for Category, used by Writers and Super Admins.

    The price levels' non-negativity is enforced by the model fields'
    MinValueValidator, which ModelSerializer picks up automatically - no
    duplicate validation here. Includes read-only `id` so a client creating a
    category gets it back without a second GET round-trip.

    Persistence is delegated to category_service so the acting user is recorded
    on created_by/updated_by in one place.
    """

    class Meta:
        model = Category
        fields = [
            "id",
            "name",
            "description",
            "creator_price_beginner",
            "creator_price_intermediate",
            "creator_price_advanced",
            "icon",
            "track_preference",
            "status",
        ]
        read_only_fields = ["id"]
        extra_kwargs = {
            "name": {
                "help_text": (
                    "Display name shown to creators when picking a category. "
                    "Must be unique across all categories."
                )
            },
            "description": {
                "help_text": (
                    "Explains to creators what belongs in this category. "
                    "Optional - omit or send an empty string for none."
                )
            },
            "creator_price_beginner": {
                "help_text": (
                    "Payout for an approved Beginner course, as a decimal "
                    'string (e.g. "500.00"). Zero or greater. Also the rate '
                    "a brand-new topic in this category inherits."
                )
            },
            "creator_price_intermediate": {
                "help_text": (
                    "Payout for an approved Intermediate course, as a "
                    "decimal string. Zero or greater."
                )
            },
            "creator_price_advanced": {
                "help_text": (
                    "Payout for an approved Advanced course, as a decimal "
                    "string. Zero or greater. Price changes are never "
                    "retroactive: a course freezes the rate in effect when "
                    "it was submitted, so existing payouts are unaffected."
                )
            },
            "icon": {
                "help_text": (
                    "Icon identifier for the category picker. Free text - "
                    "the client owns its own icon set."
                )
            },
            "track_preference": {
                "help_text": (
                    "Which production track suits this category: "
                    "`CREATOR_PREFERRED`, `AI_PREFERRED`, or `OPEN`. "
                    "Defaults to `OPEN` when omitted."
                )
            },
            "status": {
                "help_text": (
                    "`ACTIVE` accepts new course submissions; `INACTIVE` "
                    "pauses it; `ARCHIVED` retires it from the creator "
                    "picker entirely. None of them delete anything or affect "
                    "courses already in flight. Defaults to `ACTIVE`."
                )
            },
        }

    def create(self, validated_data):
        return category_service.create_category(
            actor=self.context["request"].user, **validated_data
        )

    def update(self, instance, validated_data):
        return category_service.update_category(
            category=instance,
            actor=self.context["request"].user,
            data=validated_data,
        )

    def to_representation(self, instance):
        return CategorySerializer(instance, context=self.context).data


class CategoryDeletionImpactSerializer(serializers.Serializer):
    """What deleting a category would take with it.

    Read by the delete confirmation dialog so the admin sees the damage before
    committing to it, not after.
    """

    category_id = serializers.UUIDField(
        read_only=True, help_text="The category being examined."
    )
    category_name = serializers.CharField(
        read_only=True, help_text="Its display name, for the warning copy."
    )
    course_count = serializers.IntegerField(
        read_only=True,
        help_text=(
            "How many courses belong to this category. Zero means it can be "
            "deleted outright with no further questions."
        ),
    )
    courses_by_status = serializers.DictField(
        read_only=True,
        child=serializers.IntegerField(),
        help_text=(
            "Course counts keyed by status, e.g. "
            '{"DRAFT": 3, "PUBLISHED": 1}. Statuses with no courses are '
            "omitted. Use this to warn harder when published work is at risk."
        ),
    )
    affected_creator_profile_count = serializers.IntegerField(
        read_only=True,
        help_text=(
            "Onboarding profiles naming this as their primary expertise. These "
            "never block deletion and are never deleted - they simply lose the "
            "value, under either strategy."
        ),
    )
    requires_strategy = serializers.BooleanField(
        read_only=True,
        help_text=(
            "True when DELETE needs a `strategy`. False means a plain DELETE "
            "will succeed."
        ),
    )


class CategoryDeletionSerializer(serializers.Serializer):
    """Query parameters accepted by DELETE /categories/{id}/."""

    strategy = serializers.ChoiceField(
        choices=CategoryDeletionStrategy.choices,
        required=False,
        allow_null=True,
        help_text=(
            "What to do with the category's courses. `REASSIGN` moves them to "
            "`replacement_category`; `DELETE_COURSES` deletes them along with "
            "their modules, lessons, assessments, and review history. Omit "
            "only when the category has no courses - otherwise the request is "
            "rejected with 409 rather than a default being assumed."
        ),
    )
    replacement_category = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.all(),
        required=False,
        allow_null=True,
        help_text=(
            "Category to move the courses into. Required when `strategy` is "
            "`REASSIGN`, ignored otherwise. Cannot be the category being "
            "deleted."
        ),
    )


class CategoryStatsSerializer(serializers.Serializer):
    """Header tiles above the categories table."""

    total = serializers.IntegerField(help_text="Every category, whatever its status.")
    active = serializers.IntegerField(help_text="Accepting new course submissions.")
    inactive = serializers.IntegerField(help_text="Temporarily paused.")
    archived = serializers.IntegerField(help_text="Retired from the creator picker.")

from rest_framework import serializers

from api.catalog.models import Category, Topic
from api.courses.models import AIGenerationItem, AIGenerationJob


class AIGenerationItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = AIGenerationItem
        fields = [
            "id",
            "key",
            "label",
            "phase",
            "status",
            "order",
            "target_type",
            "target_id",
            "error_message",
        ]


class AIGenerationJobSerializer(serializers.ModelSerializer):
    items = AIGenerationItemSerializer(many=True, read_only=True)
    builder_ready = serializers.SerializerMethodField()
    current_phase = serializers.SerializerMethodField()

    class Meta:
        model = AIGenerationJob
        fields = [
            "id",
            "course",
            "kind",
            "status",
            "stage",
            "current_phase",
            "result",
            "error_message",
            "cancel_requested",
            "builder_ready",
            "items",
            "created_datetime",
            "updated_datetime",
        ]
        read_only_fields = fields

    def get_builder_ready(self, obj) -> bool:
        return obj.course_id is not None

    def get_current_phase(self, obj) -> str | None:
        if obj.kind != "FULL_COURSE":
            return None
        return "PREPARING_DETAILS" if obj.course_id else "CREATING_CONTENT"


class AICourseGenerationCreateSerializer(serializers.Serializer):
    title = serializers.CharField(
        max_length=255,
        required=False,
        help_text="Course title entered on the first Create with AI screen.",
    )
    course_title = serializers.CharField(
        max_length=255,
        required=False,
        write_only=True,
        help_text="Deprecated alias for title; new clients should send title.",
    )
    description = serializers.CharField(
        help_text="Creator's plain-language description of the intended course."
    )
    category = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.all(),
        help_text="UUID of the selected course category.",
    )
    topic = serializers.PrimaryKeyRelatedField(
        queryset=Topic.objects.all(),
        required=False,
        allow_null=True,
        help_text="Optional UUID of a topic belonging to the selected category.",
    )
    terms_accepted = serializers.BooleanField(
        help_text="Whether the creator accepted the category terms."
    )
    idempotency_key = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
        help_text="Client-generated key reused when retrying the same submission.",
    )

    def validate(self, attrs):
        title = attrs.pop("title", None)
        legacy_title = attrs.get("course_title")
        if title and legacy_title and title != legacy_title:
            raise serializers.ValidationError(
                {"title": "title and course_title must match when both are supplied."}
            )
        if not title and not legacy_title:
            raise serializers.ValidationError({"title": "This field is required."})
        attrs["course_title"] = title or legacy_title
        if not attrs["terms_accepted"]:
            raise serializers.ValidationError(
                {"terms_accepted": "You must accept the category Terms and Conditions."}
            )
        topic = attrs.get("topic")
        if topic and topic.category_id != attrs["category"].id:
            raise serializers.ValidationError(
                {"topic": "Topic does not belong to the selected category."}
            )
        attrs["category_name"] = attrs["category"].name
        attrs["topic_name"] = topic.name if topic else ""
        return attrs


class AIAssistCreateSerializer(serializers.Serializer):
    target_type = serializers.ChoiceField(
        choices=["course", "module", "lesson"],
        help_text="Type of course-builder record whose field should be improved.",
    )
    target_id = serializers.UUIDField(
        help_text="UUID of the course, module, or lesson selected by target_type."
    )
    field = serializers.CharField(
        max_length=64,
        help_text="Editable field to improve, such as title or learning_objectives.",
    )
    current_value = serializers.JSONField(
        required=False,
        help_text="Current field value supplied as context; omit when the field is empty.",
    )
    instruction = serializers.CharField(
        max_length=2000,
        help_text="Plain-language instruction describing the requested improvement.",
    )
    target_updated_at = serializers.DateTimeField(
        help_text="Last known target update time in ISO 8601 format for conflict detection."
    )


class AIThumbnailCreateSerializer(serializers.Serializer):
    prompt = serializers.CharField(
        max_length=2000,
        help_text="Visual description for the generated course thumbnail.",
    )


class AIAssistApplyResponseSerializer(serializers.Serializer):
    field = serializers.CharField(read_only=True)
    value = serializers.JSONField(read_only=True)
    updated_datetime = serializers.DateTimeField(read_only=True)


class AIThumbnailApplyResponseSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    url = serializers.CharField(read_only=True)

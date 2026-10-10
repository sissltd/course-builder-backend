from rest_framework import serializers

from api.courses.enums import DistributionChannel
from api.production.enums import DeliveryMethod
from api.production.models import ChannelMapping, ProductionRun


class ProductionRunCourseSerializer(serializers.Serializer):
    id = serializers.UUIDField(help_text="Course id.")
    title = serializers.CharField(help_text="Course title.")
    status = serializers.CharField(help_text="Course lifecycle status.")


class ProductionRunSerializer(serializers.ModelSerializer):
    """A production run as the admin pipeline screens show it."""

    course = ProductionRunCourseSerializer(read_only=True, help_text="The course being produced.")
    requested_by = serializers.EmailField(
        source="requested_by.email",
        read_only=True,
        allow_null=True,
        help_text="Email of whoever handed the course to the engine.",
    )

    class Meta:
        model = ProductionRun
        fields = (
            "id",
            "course",
            "requested_by",
            "kind",
            "status",
            "status_reason",
            "instructions",
            "quote_amount",
            "budget_amount",
            "spent_amount",
            "attempts",
            "started_at",
            "finished_at",
            "created_datetime",
            "updated_datetime",
        )
        read_only_fields = fields
        extra_kwargs = {
            "kind": {"help_text": "VIDEO (production or rework) or PACKAGE (final package and channel delivery)."},
            "instructions": {
                "help_text": (
                    'For a rework: {"lessons": {"<lesson id>": ["REVOICE" | "RESTORYBOARD" | "RERENDER"]}, '
                    '"flags": ["<review flag id>"]}. Empty for a first production.'
                )
            },
            "status": {"help_text": "QUEUED, RUNNING, BLOCKED, COMPLETED, FAILED or CANCELLED."},
            "status_reason": {"help_text": "Why the run is blocked, failed, paused or cancelled."},
            "quote_amount": {"help_text": "Estimated cost of the run in USD, made before any spend."},
            "budget_amount": {"help_text": "Per-course budget in force when the run was quoted, USD."},
            "spent_amount": {"help_text": "Actual spend so far, USD."},
            "attempts": {"help_text": "Times a worker has started the run."},
            "started_at": {"help_text": "When a worker first started it."},
            "finished_at": {"help_text": "When it completed, failed, was blocked or cancelled."},
        }


class ProductionRunLessonSerializer(serializers.Serializer):
    lesson_id = serializers.UUIDField(help_text="Lesson id.")
    title = serializers.CharField(help_text="Lesson title.")
    scene_count = serializers.IntegerField(help_text="Storyboard scenes planned for it (0 = not yet).")
    steps_completed = serializers.IntegerField(help_text="Steps this run finished for it (storyboard, narration, visuals, render, quality check).")
    steps_failed = serializers.IntegerField(help_text="Quality checks this run failed for it (each is retried once).")


class ProductionRunDetailSerializer(ProductionRunSerializer):
    """A run plus the per-lesson progress of its steps."""

    lessons = ProductionRunLessonSerializer(
        many=True, read_only=True, help_text="Every lesson of the course, in order, with its progress."
    )

    class Meta(ProductionRunSerializer.Meta):
        fields = ProductionRunSerializer.Meta.fields + ("lessons",)
        read_only_fields = fields


class ChannelMappingSerializer(serializers.ModelSerializer):
    """One version of a channel's mapping."""

    created_by = serializers.EmailField(
        source="created_by.email", read_only=True, allow_null=True, help_text="Who saved it; null for the seeded version 1."
    )

    class Meta:
        model = ChannelMapping
        fields = (
            "id", "channel", "version", "delivery_method", "target_schema", "field_map",
            "response_id_path", "is_active", "notes", "created_by", "created_datetime",
        )
        read_only_fields = fields
        extra_kwargs = {
            "channel": {"help_text": "SOLUDESK, UDEMY or COURSERA."},
            "version": {"help_text": "Version number; versions are never edited."},
            "delivery_method": {"help_text": "API_PUSH or UPLOAD_KIT."},
            "target_schema": {"help_text": "JSON Schema of what the channel accepts."},
            "field_map": {"help_text": "Target field path -> rule (from / const / each)."},
            "response_id_path": {"help_text": "API push: where the channel's course id is in its response."},
            "is_active": {"help_text": "Whether deliveries use this version (one per channel)."},
            "notes": {"help_text": "What changed in this version, and why."},
        }


class ChannelMappingCreateSerializer(serializers.Serializer):
    channel = serializers.ChoiceField(choices=DistributionChannel.choices, help_text="The channel this mapping describes.")
    delivery_method = serializers.ChoiceField(choices=DeliveryMethod.choices, help_text="API_PUSH or UPLOAD_KIT.")
    target_schema = serializers.JSONField(help_text="JSON Schema (draft 2020-12) of what the channel accepts.")
    field_map = serializers.JSONField(help_text="Target field path -> rule; see the endpoint description.")
    response_id_path = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=100,
        help_text="API push only: where the channel's course id is in its response, e.g. 'data.id'.",
    )
    notes = serializers.CharField(required=False, allow_blank=True, default="", help_text="What this version changes.")
    activate = serializers.BooleanField(required=False, default=False, help_text="Make it the channel's active version now.")

    def validate_target_schema(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("Must be a JSON object.")
        return value


class ChannelMappingPreviewRequestSerializer(serializers.Serializer):
    course_id = serializers.UUIDField(help_text="Course to shape with this mapping (nothing is delivered).")


class ChannelMappingPreviewSerializer(serializers.Serializer):
    payload = serializers.DictField(help_text="What the channel would receive.")
    gaps = serializers.ListField(child=serializers.CharField(), help_text="Every way the payload falls short of the channel's schema; empty when deliverable.")


class PackageFileSerializer(serializers.Serializer):
    kind = serializers.CharField(help_text="PACKAGE, SCORM_12, SCORM_2004 or UPLOAD_KIT.")
    channel = serializers.CharField(help_text="For an upload kit, its channel; otherwise empty.")
    size_bytes = serializers.IntegerField(help_text="File size.")
    built_at = serializers.DateTimeField(help_text="When it was built.")
    url = serializers.CharField(help_text="Download link, valid for 10 minutes.")


class PackageLessonSerializer(serializers.Serializer):
    lesson_id = serializers.UUIDField(help_text="Lesson id.")
    title = serializers.CharField(help_text="Lesson title.")
    video_url = serializers.CharField(help_text="Video download link, valid for 10 minutes (or the external link).")
    captions_vtt_url = serializers.CharField(help_text="WebVTT captions link, when there are captions.")
    captions_srt_url = serializers.CharField(help_text="SRT captions link, when there are captions.")


class PackageSerializer(serializers.Serializer):
    files = PackageFileSerializer(many=True, help_text="The newest package, SCORM exports and upload kits.")
    lessons = PackageLessonSerializer(many=True, help_text="Each lesson's media, for kit uploads.")


class DistributionRecordSerializer(serializers.Serializer):
    external_course_id = serializers.CharField(max_length=255, help_text="The channel's own id for the uploaded course.")


class DistributionSerializer(serializers.Serializer):
    id = serializers.UUIDField(help_text="Distribution id.")
    channel = serializers.CharField(help_text="SOLUDESK, UDEMY or COURSERA.")
    status = serializers.CharField(help_text="DRAFT, QUEUED, PUBLISHED or FAILED.")
    external_course_id = serializers.CharField(help_text="The channel's id for the course.")
    failure_reason = serializers.CharField(help_text="Why delivery failed, when it did.")
    published_at = serializers.DateTimeField(allow_null=True, help_text="When it went live there.")

from rest_framework import serializers

from api.courses.models import (
    MediaAsset,
    QualityCheckRun,
    QualityFinding,
    ReviewAssignment,
    ReviewComment,
)


class ReviewerMiniSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    email = serializers.EmailField(read_only=True)


class ReviewAssignmentSerializer(serializers.ModelSerializer):
    reviewer = ReviewerMiniSerializer(read_only=True)

    class Meta:
        model = ReviewAssignment
        fields = ["id", "stage", "reviewer", "claimed_at", "completed_at"]
        read_only_fields = fields


class QualityFindingSerializer(serializers.ModelSerializer):
    class Meta:
        model = QualityFinding
        fields = [
            "id",
            "code",
            "severity",
            "message",
            "module",
            "lesson",
            "evidence",
            "resolved_at",
            "created_datetime",
        ]
        read_only_fields = fields


class QualityCheckRunSerializer(serializers.ModelSerializer):
    findings = QualityFindingSerializer(many=True, read_only=True)

    class Meta:
        model = QualityCheckRun
        fields = [
            "id",
            "provider",
            "overall_score",
            "risk_level",
            "status",
            "plagiarism_status",
            "plagiarism_score",
            "duplicate_status",
            "duplicate_score",
            "raw_report",
            "findings",
            "created_datetime",
        ]
        read_only_fields = fields


class ReviewCommentSerializer(serializers.ModelSerializer):
    reviewer = ReviewerMiniSerializer(read_only=True)

    class Meta:
        model = ReviewComment
        fields = [
            "id",
            "reviewer",
            "stage",
            "module",
            "lesson",
            "severity",
            "reason_code",
            "comment",
            "resolved_at",
            "created_datetime",
        ]
        read_only_fields = ["id", "reviewer", "resolved_at", "created_datetime"]


class ReviewCommentCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReviewComment
        fields = ["stage", "module", "lesson", "severity", "reason_code", "comment"]
        extra_kwargs = {
            "stage": {
                "help_text": "Review gate the comment belongs to: CONTENT or QA.",
            },
            "module": {
                "help_text": "Optional module UUID the comment concerns; omit for a course-level comment.",
            },
            "lesson": {
                "help_text": "Optional lesson UUID the comment concerns; omit when it does not apply to one lesson.",
            },
            "severity": {
                "help_text": "Impact level of the comment: INFO, WARNING, or ERROR.",
            },
            "reason_code": {
                "help_text": "Optional stable code the client can use to group similar review findings.",
            },
            "comment": {
                "help_text": "Clear, actionable explanation of the issue or review observation.",
            },
        }

    def validate(self, attrs):
        course = self.context["course"]
        module = attrs.get("module")
        lesson = attrs.get("lesson")
        if module and module.course_id != course.id:
            raise serializers.ValidationError(
                {"module": "Module does not belong to this course."}
            )
        if lesson and lesson.module.course_id != course.id:
            raise serializers.ValidationError(
                {"lesson": "Lesson does not belong to this course."}
            )
        if lesson and module and lesson.module_id != module.id:
            raise serializers.ValidationError(
                {"lesson": "Lesson does not belong to the selected module."}
            )
        return attrs


class MediaAssetSerializer(serializers.ModelSerializer):
    verified_by = ReviewerMiniSerializer(read_only=True)

    class Meta:
        model = MediaAsset
        fields = [
            "id",
            "lesson",
            "kind",
            "url",
            "mime_type",
            "duration_seconds",
            "resolution",
            "subtitle_url",
            "caption_accuracy_percent",
            "audio_lufs",
            "audio_video_drift_ms",
            "accessibility",
            "verification",
            "verified_at",
            "verified_by",
        ]
        read_only_fields = ["id", "verified_at", "verified_by"]
        extra_kwargs = {
            "lesson": {
                "help_text": "Optional lesson UUID that owns this asset; omit for course-level preview videos and thumbnails.",
            },
            "kind": {
                "help_text": "Asset type: VIDEO, AUDIO, SUBTITLE, THUMBNAIL, or PREVIEW_VIDEO.",
            },
            "url": {
                "help_text": "HTTPS URL where the QA reviewer can access the asset.",
            },
            "mime_type": {
                "help_text": "Optional MIME type such as video/mp4 or image/jpeg; omit if it is unknown.",
            },
            "duration_seconds": {
                "help_text": "Optional whole-number media duration in seconds; omit for still images.",
            },
            "resolution": {
                "help_text": "Optional pixel dimensions in WIDTHxHEIGHT format, such as 1920x1080.",
            },
            "subtitle_url": {
                "help_text": "Optional HTTPS URL to the captions or subtitle file; omit when no subtitle file exists.",
            },
            "caption_accuracy_percent": {
                "help_text": "Optional caption-accuracy percentage from 0.00 to 100.00.",
            },
            "audio_lufs": {
                "help_text": "Optional integrated audio loudness in LUFS, for example -16.00.",
            },
            "audio_video_drift_ms": {
                "help_text": "Optional maximum observed audio/video drift in milliseconds.",
            },
            "accessibility": {
                "help_text": "Optional accessibility metadata object, such as captions or thumbnail alt text.",
            },
            "verification": {
                "help_text": "Optional source-system verification metadata; omit when no verification data is available.",
            },
        }

    def validate(self, attrs):
        course = self.context.get("course")
        lesson = attrs.get("lesson")
        if course and lesson and lesson.module.course_id != course.id:
            raise serializers.ValidationError(
                {"lesson": "Lesson does not belong to this course."}
            )
        return attrs


class QAApprovalSerializer(serializers.Serializer):
    feedback = serializers.JSONField(
        required=False,
        default=dict,
        help_text="Optional JSON feedback retained with the QA approval; an empty object is used when omitted.",
    )


class QARejectSerializer(serializers.Serializer):
    feedback = serializers.JSONField(
        help_text="JSON feedback containing a non-empty summary that explains why QA rejected the course.",
    )

    def validate_feedback(self, value):
        if not isinstance(value, dict) or not value.get("summary"):
            raise serializers.ValidationError("feedback.summary is required.")
        return value

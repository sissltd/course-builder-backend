from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from api.catalog.serializers.category_serializer import CategoryMiniSerializer
from api.catalog.serializers.topic_serializer import TopicMiniSerializer
from api.courses.models import Course, CourseDistribution, CourseVersion
from api.courses.serializers.assessment_serializer import AssessmentSerializer
from api.courses.serializers.module_serializer import ModuleSerializer
from api.reviews.serializers import (
    MediaAssetSerializer,
    QualityCheckRunSerializer,
    QualityFindingSerializer,
    ReviewAssignmentSerializer,
    ReviewCommentSerializer,
)
from api.courses.services import course_service
from api.reviews.serializers import ReviewActionSerializer  # noqa: F401 (re-export)


def _validate_string_list(value, field_name):
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise serializers.ValidationError(
            f"{field_name} must be a list of non-empty strings."
        )
    return value


class CourseVersionSerializer(serializers.ModelSerializer):
    """Active version option shown on the course builder's Versioning step."""

    class Meta:
        model = CourseVersion
        fields = ["id", "label"]
        read_only_fields = fields


class CourseListSerializer(serializers.ModelSerializer):
    """Compact Course representation for list/queue views."""

    category = CategoryMiniSerializer(read_only=True)
    topic = TopicMiniSerializer(read_only=True)
    review_stage = serializers.SerializerMethodField()
    assigned_reviewer = serializers.SerializerMethodField()
    quality = serializers.SerializerMethodField()
    issue_count = serializers.SerializerMethodField()
    waiting_seconds = serializers.SerializerMethodField()
    creator = serializers.SerializerMethodField()
    course_id = serializers.UUIDField(source="id", read_only=True)
    course_title = serializers.CharField(source="title", read_only=True)
    reviewed_by = serializers.SerializerMethodField()
    display_status = serializers.SerializerMethodField()
    date_submitted = serializers.DateTimeField(source="submitted_at", read_only=True)
    date_approved = serializers.DateTimeField(source="approved_at", read_only=True)

    class Meta:
        model = Course
        fields = [
            "id",
            "title",
            "course_id",
            "course_title",
            "creator",
            "category",
            "topic",
            "status",
            "display_status",
            "source_type",
            "quality_score",
            "review_stage",
            "assigned_reviewer",
            "reviewed_by",
            "difficulty_level",
            "quality",
            "issue_count",
            "waiting_seconds",
            "creator_price_snapshot",
            "submitted_at",
            "date_submitted",
            "date_approved",
            "created_datetime",
            "updated_datetime",
        ]
        read_only_fields = fields

    def get_creator(self, obj) -> dict:
        return {
            "id": obj.creator_id,
            "first_name": obj.creator.first_name,
            "last_name": obj.creator.last_name,
            "email": obj.creator.email,
        }

    def get_reviewed_by(self, obj) -> dict | None:
        return self.get_assigned_reviewer(obj)

    def get_display_status(self, obj) -> str:
        return "PENDING" if obj.status == "SUBMITTED" else obj.status

    def get_review_stage(self, obj) -> str:
        return "QA" if obj.status == "QA_VERIFICATION" else "CONTENT"

    def get_assigned_reviewer(self, obj) -> dict | None:
        stage = self.get_review_stage(obj)
        assignment = next(
            (item for item in obj.review_assignments.all() if item.stage == stage), None
        )
        if not assignment or not assignment.reviewer_id:
            return None
        return {
            "id": assignment.reviewer_id,
            "first_name": assignment.reviewer.first_name,
            "last_name": assignment.reviewer.last_name,
            "email": assignment.reviewer.email,
        }

    def get_quality(self, obj) -> dict:
        run = next(iter(obj.quality_check_runs.all()), None)
        if not run:
            return {
                "status": "NOT_RUN",
                "plagiarism_status": "NOT_RUN",
                "duplicate_status": "NOT_RUN",
            }
        return {
            "overall_score": run.overall_score,
            "risk_level": run.risk_level,
            "status": run.status,
            "plagiarism_status": run.plagiarism_status,
            "plagiarism_score": run.plagiarism_score,
            "duplicate_status": run.duplicate_status,
            "duplicate_score": run.duplicate_score,
        }

    def get_issue_count(self, obj) -> int:
        return sum(
            1
            for item in obj.quality_findings.all()
            if item.resolved_at is None and item.severity in {"WARNING", "ERROR"}
        )

    def get_waiting_seconds(self, obj) -> int:
        from django.utils import timezone

        stage = self.get_review_stage(obj)
        assignment = next(
            (item for item in obj.review_assignments.all() if item.stage == stage), None
        )
        started_at = (
            assignment.claimed_at
            if assignment and assignment.claimed_at
            else obj.submitted_at
        )
        return (
            max(0, int((timezone.now() - started_at).total_seconds()))
            if started_at
            else 0
        )


class ComparableCourseSerializer(serializers.Serializer):
    """One row under Related Courses / Comparable Courses in the design."""

    course_title = serializers.CharField(help_text="Comparable course title.")
    difficulty_level = serializers.ChoiceField(
        choices=["BEGINNER", "INTERMEDIATE", "ADVANCED"],
        help_text="Comparable course difficulty level.",
    )
    learner_price = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=0,
        help_text="Comparable course learner price.",
    )


class CourseDistributionSerializer(serializers.ModelSerializer):
    """Read shape using the labels from the Figma Review Prices cards."""

    creator_payout_fixed = serializers.DecimalField(
        source="course.creator_price_snapshot",
        max_digits=12,
        decimal_places=2,
        allow_null=True,
        read_only=True,
        help_text="Fixed creator payout captured when the course was submitted.",
    )
    learner_fee = serializers.DecimalField(
        source="learner_price",
        max_digits=12,
        decimal_places=2,
        read_only=True,
        help_text="Learner fee shown in the Course Fees summary.",
    )
    mie_suggestion = serializers.DecimalField(
        source="mie_suggested_price",
        max_digits=12,
        decimal_places=2,
        allow_null=True,
        read_only=True,
        help_text="MIE-suggested learner price shown below the price input.",
    )
    model = serializers.CharField(
        source="pricing_model",
        read_only=True,
        help_text="Selected commercial model: ONE_TIME, SUBSCRIPTION, PROMOTIONAL, or B2B_ONLY.",
    )
    course_fee_percent = serializers.DecimalField(
        source="marketplace_fee_percent",
        max_digits=5,
        decimal_places=2,
        allow_null=True,
        read_only=True,
        help_text="Coursera/Udemy course fee as a percentage of net revenue.",
    )
    promotional_pricing = serializers.DecimalField(
        source="promotional_price",
        max_digits=12,
        decimal_places=2,
        allow_null=True,
        read_only=True,
        help_text="Promotional pricing displayed for the marketplace channel.",
    )
    mie_explanation = serializers.CharField(
        source="mie_rationale",
        read_only=True,
        help_text="MIE explanation displayed in the blue recommendation panel.",
    )
    comparable_courses = ComparableCourseSerializer(many=True, read_only=True)

    class Meta:
        model = CourseDistribution
        fields = [
            "id",
            "channel",
            "approval_rate",
            "learner_price",
            "mie_suggestion",
            "model",
            "learner_fee",
            "creator_payout_fixed",
            "course_fee_percent",
            "promotional_pricing",
            "platform_revenue_per_enrollment",
            "mie_explanation",
            "comparable_courses",
            "status",
            "external_course_id",
            "failure_reason",
            "published_at",
        ]
        read_only_fields = [
            "id",
            "creator_payout_fixed",
            "status",
            "external_course_id",
            "failure_reason",
            "published_at",
        ]


class CourseDistributionInputSerializer(serializers.ModelSerializer):
    """Editable fields in a SoluDesk, Coursera, or Udemy pricing tab."""

    mie_suggestion = serializers.DecimalField(
        source="mie_suggested_price",
        max_digits=12,
        decimal_places=2,
        min_value=0,
        required=False,
        allow_null=True,
        help_text="MIE-suggested price displayed beneath Learner price.",
    )
    model = serializers.ChoiceField(
        source="pricing_model",
        choices=["ONE_TIME", "SUBSCRIPTION", "PROMOTIONAL", "B2B_ONLY"],
        default="ONE_TIME",
        help_text="Pricing option selected in the design.",
    )
    course_fee_percent = serializers.DecimalField(
        source="marketplace_fee_percent",
        max_digits=5,
        decimal_places=2,
        min_value=0,
        max_value=100,
        required=False,
        allow_null=True,
        help_text="Coursera/Udemy course fee percentage.",
    )
    promotional_pricing = serializers.DecimalField(
        source="promotional_price",
        max_digits=12,
        decimal_places=2,
        min_value=0,
        required=False,
        allow_null=True,
        help_text="Promotional pricing for Coursera or Udemy.",
    )
    mie_explanation = serializers.CharField(
        source="mie_rationale",
        required=False,
        allow_blank=True,
        help_text="Text displayed in the MIE recommendation panel.",
    )
    comparable_courses = ComparableCourseSerializer(many=True, required=False)

    def validate_comparable_courses(self, value):
        """Store JSON-safe values while retaining decimal validation."""

        return ComparableCourseSerializer(value, many=True).data

    class Meta:
        model = CourseDistribution
        fields = [
            "channel",
            "approval_rate",
            "learner_price",
            "mie_suggestion",
            "model",
            "course_fee_percent",
            "promotional_pricing",
            "platform_revenue_per_enrollment",
            "mie_explanation",
            "comparable_courses",
        ]


class ReviewAndPublishSerializer(serializers.Serializer):
    distribution_channels = CourseDistributionInputSerializer(many=True)

    def validate_distribution_channels(self, value):
        channels = [item["channel"] for item in value]
        if len(channels) != len(set(channels)):
            raise serializers.ValidationError("Each channel may appear only once.")
        if not channels:
            raise serializers.ValidationError(
                "Select at least one distribution channel."
            )
        return value


class CourseDetailSerializer(serializers.ModelSerializer):
    """Full Course representation, including nested modules/lessons/assessments."""

    category = CategoryMiniSerializer(read_only=True)
    topic = TopicMiniSerializer(read_only=True)
    modules = ModuleSerializer(many=True, read_only=True)
    final_assessment = serializers.SerializerMethodField()
    media_assets = MediaAssetSerializer(many=True, read_only=True)
    quality_check_runs = QualityCheckRunSerializer(many=True, read_only=True)
    quality_findings = QualityFindingSerializer(many=True, read_only=True)
    review_assignments = ReviewAssignmentSerializer(many=True, read_only=True)
    review_comments = ReviewCommentSerializer(many=True, read_only=True)
    qa_video_samples = serializers.SerializerMethodField()
    distribution_channels = CourseDistributionSerializer(many=True, read_only=True)
    version = CourseVersionSerializer(read_only=True)

    class Meta:
        model = Course
        fields = [
            "id",
            "title",
            "description",
            "category",
            "topic",
            "difficulty_level",
            "source_type",
            "quality_score",
            "learning_objectives",
            "tags",
            "planned_duration_seconds",
            "status",
            "creator_price_snapshot",
            "preview_video_url",
            "thumbnail_url",
            "terms_accepted_at",
            "submitted_at",
            "approved_at",
            "published_at",
            "rejected_at",
            "modules",
            "final_assessment",
            "media_assets",
            "quality_check_runs",
            "quality_findings",
            "review_assignments",
            "review_comments",
            "qa_video_samples",
            "distribution_channels",
            "duration_estimate_minutes",
            "version",
            "created_datetime",
            "updated_datetime",
        ]
        read_only_fields = fields

    @extend_schema_field(AssessmentSerializer(allow_null=True))
    def get_final_assessment(self, obj):
        assessment = getattr(obj, "final_assessment", None)
        return AssessmentSerializer(assessment).data if assessment else None

    def get_qa_video_samples(self, obj) -> list:
        """Expose intro, middle, and conclusion video evidence for QA playback."""

        videos = sorted(
            (asset for asset in obj.media_assets.all() if asset.kind == "VIDEO"),
            key=lambda asset: (
                asset.lesson.module.order if asset.lesson_id else -1,
                asset.lesson.order if asset.lesson_id else -1,
            ),
        )
        if not videos:
            return []
        indexes = sorted({0, len(videos) // 2, len(videos) - 1})
        labels = {0: "INTRO", len(videos) // 2: "MIDDLE", len(videos) - 1: "CONCLUSION"}
        return [
            {"sample": labels[index], "asset": MediaAssetSerializer(videos[index]).data}
            for index in indexes
        ]


class CourseCreateSerializer(serializers.ModelSerializer):
    """Write serializer for creating a Draft course.

    Delegates persistence to course_service.create_draft_course so BR-005
    (terms acceptance), category-status validation, and topic/category
    consistency all live in one place.
    """

    terms_accepted = serializers.BooleanField(write_only=True)
    duration_hours = serializers.IntegerField(
        required=False, default=0, min_value=0, write_only=True
    )
    duration_minutes = serializers.IntegerField(
        required=False, default=0, min_value=0, write_only=True
    )
    duration_seconds = serializers.IntegerField(
        required=False, default=0, min_value=0, write_only=True
    )
    version = serializers.PrimaryKeyRelatedField(
        queryset=CourseVersion.objects.filter(is_active=True), required=False
    )

    class Meta:
        model = Course
        fields = [
            "category",
            "topic",
            "title",
            "description",
            "preview_video_url",
            "thumbnail_url",
            "difficulty_level",
            "learning_objectives",
            "tags",
            "version",
            "duration_hours",
            "duration_minutes",
            "duration_seconds",
            "terms_accepted",
        ]
        extra_kwargs = {"topic": {"required": False}}

    def validate_learning_objectives(self, value):
        return _validate_string_list(value, "learning_objectives")

    def validate_tags(self, value):
        return _validate_string_list(value, "tags")

    def create(self, validated_data):
        request = self.context["request"]
        return course_service.create_draft_course(
            creator=request.user,
            category=validated_data["category"],
            topic=validated_data.get("topic"),
            title=validated_data["title"],
            description=validated_data["description"],
            preview_video_url=validated_data.get("preview_video_url", ""),
            thumbnail_url=validated_data.get("thumbnail_url", ""),
            difficulty_level=validated_data.get("difficulty_level", ""),
            learning_objectives=validated_data.get("learning_objectives"),
            tags=validated_data.get("tags"),
            version=validated_data.get("version"),
            duration_hours=validated_data.get("duration_hours", 0),
            duration_minutes=validated_data.get("duration_minutes", 0),
            duration_seconds=validated_data.get("duration_seconds", 0),
            terms_accepted=validated_data["terms_accepted"],
        )

    def to_representation(self, instance):
        return CourseDetailSerializer(instance, context=self.context).data


class CourseUpdateSerializer(serializers.ModelSerializer):
    """Write serializer for updating a Draft course.

    Delegates to course_service.update_draft_course, which enforces that only
    Draft courses can be edited and that a supplied topic belongs to the
    course's category. duration_hours/minutes/seconds deliberately have no
    `default`, so omitting all three on a partial update leaves
    planned_duration_seconds untouched instead of zeroing it out; the wizard
    UI presents all three together, so supplying any one recombines all three
    (missing ones treated as 0 for that call).
    """

    duration_hours = serializers.IntegerField(
        required=False, min_value=0, write_only=True
    )
    duration_minutes = serializers.IntegerField(
        required=False, min_value=0, write_only=True
    )
    duration_seconds = serializers.IntegerField(
        required=False, min_value=0, write_only=True
    )
    version = serializers.PrimaryKeyRelatedField(
        queryset=CourseVersion.objects.filter(is_active=True), required=False
    )

    class Meta:
        model = Course
        fields = [
            "title",
            "description",
            "preview_video_url",
            "thumbnail_url",
            "category",
            "topic",
            "difficulty_level",
            "learning_objectives",
            "tags",
            "version",
            "duration_hours",
            "duration_minutes",
            "duration_seconds",
        ]
        extra_kwargs = {"topic": {"required": False}}

    def validate_learning_objectives(self, value):
        return _validate_string_list(value, "learning_objectives")

    def validate_tags(self, value):
        return _validate_string_list(value, "tags")

    def update(self, instance, validated_data):
        request = self.context["request"]
        duration_fields = {"duration_hours", "duration_minutes", "duration_seconds"}
        if duration_fields & validated_data.keys():
            hours = validated_data.pop("duration_hours", 0)
            minutes = validated_data.pop("duration_minutes", 0)
            seconds = validated_data.pop("duration_seconds", 0)
            validated_data["planned_duration_seconds"] = (
                hours * 3600 + minutes * 60 + seconds
            )
        return course_service.update_draft_course(
            course=instance, actor=request.user, data=validated_data
        )

    def to_representation(self, instance):
        return CourseDetailSerializer(instance, context=self.context).data


class ReviewApproveSerializer(serializers.Serializer):
    """Request body for the review-queue approve action."""

    feedback = serializers.JSONField(required=False, default=dict)


class ReviewRejectSerializer(serializers.Serializer):
    """Request body for the review-queue reject action.

    Requires a non-empty feedback["summary"] (US-202: reviewers must leave
    detailed feedback the creator can act on). `flags` is optional: a list
    of structured issue dicts persisted as ReviewFlag rows (flag_type,
    title, system_message, reviewer_note, optional lesson_id/module_id) -
    the itemized issues the creator dashboard renders alongside the
    free-form feedback.
    """

    feedback = serializers.JSONField(required=True)
    flags = serializers.JSONField(required=False, default=list)

    def validate_feedback(self, value):
        if not isinstance(value, dict) or not value.get("summary"):
            raise serializers.ValidationError("feedback.summary is required.")

        items = value.get("items", [])
        if not isinstance(items, list):
            raise serializers.ValidationError(
                "feedback.items must be a list if provided."
            )
        for index, item in enumerate(items):
            if (
                not isinstance(item, dict)
                or "module_id" not in item
                or "comment" not in item
            ):
                raise serializers.ValidationError(
                    f"feedback.items[{index}] must include 'module_id' and 'comment'."
                )

        return value

    def validate_flags(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("flags must be a list if provided.")
        for index, flag in enumerate(value):
            if (
                not isinstance(flag, dict)
                or not flag.get("flag_type")
                or not flag.get("title")
            ):
                raise serializers.ValidationError(
                    f"flags[{index}] must include 'flag_type' and 'title'."
                )
        return value

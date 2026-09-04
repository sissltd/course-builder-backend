from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from api.catalog.serializers.category_serializer import CategoryMiniSerializer
from api.catalog.serializers.topic_serializer import TopicMiniSerializer
from api.courses.enums import CourseSourceType, DistributionChannel
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
from api.reviews.enums import ReviewActionType
from api.reviews.serializers import ReviewActionSerializer  # noqa: F401 (re-export)


_SOURCE_LABELS = {
    CourseSourceType.CREATOR_UPLOADED: "Creator",
    CourseSourceType.AI_GENERATED: "AI Created",
    CourseSourceType.DEVELOPER_API: "Developer API",
}
_CHANNEL_ORDER = (
    DistributionChannel.SOLUDESK,
    DistributionChannel.UDEMY,
    DistributionChannel.COURSERA,
)
_CHANNEL_LABELS = {
    DistributionChannel.SOLUDESK: "SoluDesk",
    DistributionChannel.UDEMY: "Udemy",
    DistributionChannel.COURSERA: "Coursera",
}
_PRICE_CHANNEL_LABELS = {
    DistributionChannel.SOLUDESK: "SoluDesk",
    DistributionChannel.UDEMY: "Udemy Marketplace",
    DistributionChannel.COURSERA: "Coursera Marketplace",
}


def _person(user) -> dict | None:
    if user is None:
        return None
    full_name = " ".join(
        part for part in (user.first_name, user.last_name) if part
    ).strip()
    return {
        "id": user.id,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "full_name": full_name or user.email,
        "email": user.email,
    }


def _review_actions(course) -> list:
    return list(course.review_actions.all())


def _latest_review_action(course, *, action=None):
    return next(
        (
            item
            for item in _review_actions(course)
            if action is None or item.action == action
        ),
        None,
    )


def _current_review_assignment(course):
    stage = "QA" if course.status == "QA_VERIFICATION" else "CONTENT"
    return next(
        (item for item in course.review_assignments.all() if item.stage == stage),
        None,
    )


def _reviewer_for(course):
    latest_action = _latest_review_action(course)
    if latest_action and latest_action.reviewer_id:
        return latest_action.reviewer
    assignment = _current_review_assignment(course)
    return assignment.reviewer if assignment and assignment.reviewer_id else None


def _last_reviewed_at(course):
    stamps = [
        item.created_datetime
        for item in _review_actions(course)
        if item.created_datetime is not None
    ]
    assignment = _current_review_assignment(course)
    if assignment:
        stamps.extend(
            stamp
            for stamp in (assignment.completed_at, assignment.claimed_at)
            if stamp is not None
        )
    return max(stamps) if stamps else None


def _reviewer_note(course) -> str:
    latest_action = _latest_review_action(course)
    if not latest_action or not isinstance(latest_action.feedback, dict):
        return ""
    summary = latest_action.feedback.get("summary", "")
    return summary if isinstance(summary, str) else ""


def _distribution_rows(course) -> list:
    rows_by_channel = {row.channel: row for row in course.distribution_channels.all()}
    return [
        rows_by_channel[channel]
        for channel in _CHANNEL_ORDER
        if channel in rows_by_channel
    ]


class ReviewerPersonSerializer(serializers.Serializer):
    """Compact person shown in reviewer tables and information drawers."""

    id = serializers.UUIDField(help_text="User UUID used by reviewer filters.")
    first_name = serializers.CharField(help_text="User's first name.")
    last_name = serializers.CharField(help_text="User's last name.")
    full_name = serializers.CharField(
        help_text="Display name shown in the Figma table."
    )
    email = serializers.EmailField(help_text="User's email address.")


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


class ReviewerCourseListSerializer(CourseListSerializer):
    """Design-aligned row shared by the four reviewer course tables."""

    reviewer = serializers.SerializerMethodField(
        help_text="Reviewer displayed on the Approved and In Review tables."
    )
    reviewer_id = serializers.SerializerMethodField(
        help_text="UUID of the displayed reviewer, or null when unassigned."
    )
    approved_by = serializers.SerializerMethodField(
        help_text="Reviewer who recorded the latest approval decision."
    )
    date_reviewed = serializers.SerializerMethodField(
        help_text="Timestamp of the latest review decision, or null."
    )
    last_reviewed_at = serializers.SerializerMethodField(
        help_text="Latest claim, completion, or decision timestamp for this course."
    )
    reviewer_note = serializers.SerializerMethodField(
        help_text="Summary note saved with the latest review decision."
    )
    price = serializers.DecimalField(
        source="creator_price_snapshot",
        max_digits=10,
        decimal_places=2,
        allow_null=True,
        read_only=True,
        help_text="Fixed creator price shown in the Published Courses table.",
    )
    channels = serializers.SerializerMethodField(
        help_text="Selected publication channel codes in Figma display order."
    )
    channel_summary = serializers.SerializerMethodField(
        help_text="Human-readable channel list shown in the Published Courses table."
    )
    source_label = serializers.SerializerMethodField(
        help_text="Figma display label for the course source."
    )
    date_created = serializers.DateTimeField(
        source="created_datetime",
        read_only=True,
        help_text="Course creation timestamp shown in Owner's Information.",
    )

    class Meta(CourseListSerializer.Meta):
        fields = CourseListSerializer.Meta.fields + [
            "reviewer",
            "reviewer_id",
            "approved_by",
            "date_reviewed",
            "last_reviewed_at",
            "reviewer_note",
            "price",
            "channels",
            "channel_summary",
            "source_label",
            "date_created",
        ]
        read_only_fields = fields

    @extend_schema_field(ReviewerPersonSerializer(allow_null=True))
    def get_reviewer(self, obj) -> dict | None:
        reviewer = _person(_reviewer_for(obj))
        return ReviewerPersonSerializer(reviewer).data if reviewer else None

    @extend_schema_field(serializers.UUIDField(allow_null=True))
    def get_reviewer_id(self, obj):
        reviewer = _reviewer_for(obj)
        return str(reviewer.id) if reviewer else None

    @extend_schema_field(ReviewerPersonSerializer(allow_null=True))
    def get_approved_by(self, obj) -> dict | None:
        action = _latest_review_action(obj, action=ReviewActionType.APPROVE)
        reviewer = _person(action.reviewer) if action and action.reviewer_id else None
        return ReviewerPersonSerializer(reviewer).data if reviewer else None

    @extend_schema_field(serializers.DateTimeField(allow_null=True))
    def get_date_reviewed(self, obj):
        action = _latest_review_action(obj)
        return action.created_datetime if action else None

    @extend_schema_field(serializers.DateTimeField(allow_null=True))
    def get_last_reviewed_at(self, obj):
        return _last_reviewed_at(obj)

    def get_reviewer_note(self, obj) -> str:
        return _reviewer_note(obj)

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_channels(self, obj) -> list[str]:
        return [row.channel for row in _distribution_rows(obj)]

    def get_channel_summary(self, obj) -> str:
        labels = [_CHANNEL_LABELS[row.channel] for row in _distribution_rows(obj)]
        if len(labels) < 2:
            return "".join(labels)
        return f"{', '.join(labels[:-1])} & {labels[-1]}"

    def get_source_label(self, obj) -> str:
        return _SOURCE_LABELS.get(obj.source_type, obj.get_source_type_display())


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

    channel_label = serializers.SerializerMethodField(
        help_text="Figma label for this publication destination."
    )

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
            "channel_label",
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
        extra_kwargs = {
            "channel": {"help_text": "Destination code: SOLUDESK, UDEMY, or COURSERA."},
            "approval_rate": {
                "help_text": "Expected publication timing displayed above pricing."
            },
            "learner_price": {
                "help_text": "Learner-facing price for this destination."
            },
            "platform_revenue_per_enrollment": {
                "help_text": "Expected platform revenue for one enrolment."
            },
            "status": {"help_text": "Local publication status for this destination."},
            "external_course_id": {
                "help_text": "Marketplace identifier once external publication succeeds."
            },
            "failure_reason": {
                "help_text": "Latest marketplace publication failure, if any."
            },
            "published_at": {
                "help_text": "When this destination finished publication, or null."
            },
        }

    def get_channel_label(self, obj) -> str:
        return _PRICE_CHANNEL_LABELS.get(obj.channel, obj.get_channel_display())


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
        extra_kwargs = {
            "channel": {"help_text": "Destination code: SOLUDESK, UDEMY, or COURSERA."},
            "approval_rate": {
                "help_text": "Expected publication timing shown in the active tab."
            },
            "learner_price": {
                "help_text": "Learner price entered for this destination."
            },
            "platform_revenue_per_enrollment": {
                "help_text": "Expected platform revenue for one enrolment."
            },
        }


class ReviewAndPublishSerializer(serializers.Serializer):
    distribution_channels = CourseDistributionInputSerializer(
        many=True,
        help_text="One unique pricing input per selected publication channel.",
    )

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


class ReviewerInformationSerializer(serializers.Serializer):
    """Fields in the Approved/In Review Course Information drawer."""

    reviewer = ReviewerPersonSerializer(
        allow_null=True,
        help_text="Reviewer displayed in the drawer, or null when unassigned.",
    )
    reviewer_id = serializers.UUIDField(
        allow_null=True,
        help_text="Reviewer UUID displayed beside the copy control.",
    )
    date_reviewed = serializers.DateTimeField(
        allow_null=True,
        help_text="Timestamp of the latest review decision.",
    )
    last_reviewed_at = serializers.DateTimeField(
        allow_null=True,
        help_text="Latest claim, completion, or decision timestamp.",
    )
    reviewer_note = serializers.CharField(
        allow_blank=True,
        help_text="Summary note recorded with the latest review decision.",
    )


class OwnerInformationSerializer(serializers.Serializer):
    """Fields in the Published Course Owner's Information drawer."""

    creator = ReviewerPersonSerializer(help_text="Course owner.")
    user_id = serializers.UUIDField(help_text="Course owner's user UUID.")
    date_created = serializers.DateTimeField(help_text="Course creation timestamp.")


class PublishedPriceInformationSerializer(serializers.Serializer):
    """One channel card in the Published Course price drawer."""

    channel = serializers.ChoiceField(
        choices=DistributionChannel.choices,
        help_text="Publication destination code.",
    )
    channel_label = serializers.CharField(
        help_text="Figma display label for the publication destination."
    )
    price = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Learner price for this destination.",
    )
    status = serializers.CharField(
        help_text="Current publication state for this destination."
    )


class ReviewerCourseDetailSerializer(CourseDetailSerializer):
    """Full course plus the exact information blocks used by reviewer drawers."""

    course_id = serializers.UUIDField(
        source="id", read_only=True, help_text="Course UUID shown in the drawer."
    )
    course_title = serializers.CharField(
        source="title", read_only=True, help_text="Course title shown in the drawer."
    )
    creator = serializers.SerializerMethodField(
        help_text="Course owner displayed in reviewer tables and drawers."
    )
    source_label = serializers.SerializerMethodField(
        help_text="Figma display label for the course source."
    )
    review_information = serializers.SerializerMethodField(
        help_text="Review Information block for Approved and In Review drawers."
    )
    owner_information = serializers.SerializerMethodField(
        help_text="Owner's Information block for the Published drawer."
    )
    price_information = serializers.SerializerMethodField(
        help_text="Per-channel cards in the Published drawer."
    )
    price = serializers.DecimalField(
        source="creator_price_snapshot",
        max_digits=10,
        decimal_places=2,
        allow_null=True,
        read_only=True,
        help_text="Fixed creator price shown in the Published table.",
    )
    channels = serializers.SerializerMethodField(
        help_text="Selected publication channel codes in Figma display order."
    )
    channel_summary = serializers.SerializerMethodField(
        help_text="Human-readable channel list shown in the Published table."
    )

    class Meta(CourseDetailSerializer.Meta):
        fields = CourseDetailSerializer.Meta.fields + [
            "course_id",
            "course_title",
            "creator",
            "source_label",
            "review_information",
            "owner_information",
            "price_information",
            "price",
            "channels",
            "channel_summary",
        ]
        read_only_fields = fields

    @extend_schema_field(ReviewerPersonSerializer())
    def get_creator(self, obj) -> dict:
        return ReviewerPersonSerializer(_person(obj.creator)).data

    def get_source_label(self, obj) -> str:
        return _SOURCE_LABELS.get(obj.source_type, obj.get_source_type_display())

    @extend_schema_field(ReviewerInformationSerializer())
    def get_review_information(self, obj) -> dict:
        reviewer = _reviewer_for(obj)
        action = _latest_review_action(obj)
        return ReviewerInformationSerializer(
            {
                "reviewer": _person(reviewer),
                "reviewer_id": reviewer.id if reviewer else None,
                "date_reviewed": action.created_datetime if action else None,
                "last_reviewed_at": _last_reviewed_at(obj),
                "reviewer_note": _reviewer_note(obj),
            }
        ).data

    @extend_schema_field(OwnerInformationSerializer())
    def get_owner_information(self, obj) -> dict:
        return OwnerInformationSerializer(
            {
                "creator": _person(obj.creator),
                "user_id": obj.creator_id,
                "date_created": obj.created_datetime,
            }
        ).data

    @extend_schema_field(PublishedPriceInformationSerializer(many=True))
    def get_price_information(self, obj) -> list[dict]:
        rows = [
            {
                "channel": row.channel,
                "channel_label": _PRICE_CHANNEL_LABELS.get(
                    row.channel, row.get_channel_display()
                ),
                "price": row.learner_price,
                "status": row.status,
            }
            for row in _distribution_rows(obj)
        ]
        return PublishedPriceInformationSerializer(rows, many=True).data

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_channels(self, obj) -> list[str]:
        return [row.channel for row in _distribution_rows(obj)]

    def get_channel_summary(self, obj) -> str:
        labels = [_CHANNEL_LABELS[row.channel] for row in _distribution_rows(obj)]
        if len(labels) < 2:
            return "".join(labels)
        return f"{', '.join(labels[:-1])} & {labels[-1]}"


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

    version = serializers.PrimaryKeyRelatedField(
        queryset=CourseVersion.objects.filter(is_active=True),
        required=False,
        allow_null=True,
        help_text=(
            "Id of the CourseVersion this course should publish under. "
            "List the options at GET /api/v1/course-versions/. Only active "
            "versions may be selected; publishing honours this choice."
        ),
    )
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
            "version",
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


class CoursePreviewSerializer(serializers.Serializer):
    """A signed, expiring link to preview a course as a student sees it."""

    preview_url = serializers.URLField(
        help_text="Open or share this. The token in the query string is the grant."
    )
    expires_at = serializers.DateTimeField(
        help_text="When the link stops working (ISO-8601, UTC)."
    )
    expires_in = serializers.IntegerField(
        help_text="Seconds of validity remaining at issue time."
    )


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

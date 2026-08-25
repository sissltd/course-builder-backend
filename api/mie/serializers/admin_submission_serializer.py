from rest_framework import serializers

from api.mie.models import CourseSubmission


class AdminSubmissionSerializer(serializers.ModelSerializer):
    """One submission as seen from the admin queue: the raw Endpoint 1
    payload, current state, decision metadata, and the recommendation
    signals admins enter."""

    developer_email = serializers.EmailField(
        source="developer.email", help_text="Owning developer account email."
    )
    developer_id = serializers.UUIDField(
        source="developer.id", help_text="Owning developer account id."
    )
    reference = serializers.CharField(
        source="public_reference",
        help_text="Public reference; suffix letter tracks current status.",
    )
    rejection_reason = serializers.CharField(
        source="rejection_reason.label",
        default=None,
        allow_null=True,
        help_text="Taxonomy label attached to the latest rejection, if any.",
    )
    decided_by_email = serializers.EmailField(
        source="decided_by.email",
        default=None,
        allow_null=True,
        help_text="Superadmin responsible for the latest decision.",
    )

    class Meta:
        model = CourseSubmission
        fields = (
            "id",
            "reference",
            "title",
            "status",
            "payload",
            "developer_id",
            "developer_email",
            "payout_bypass",
            "demand_score",
            "estimated_monthly_earnings",
            "rejection_reason",
            "rejection_note",
            "queued_at",
            "decided_at",
            "decided_by_email",
            "resulting_course",
            "created_datetime",
            "updated_datetime",
        )
        read_only_fields = fields


class SubmissionDecisionSerializer(serializers.Serializer):
    """Body for the approve/reject actions."""

    rejection_reason = serializers.CharField(
        required=False,
        help_text=(
            "Label of an existing SubmissionRejectionReason - REQUIRED when "
            "rejecting. Find labels via GET /mie/admin/rejection-reasons/ "
            "(admin) or the Django admin."
        ),
    )
    rejection_note = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Free-text detail delivered inside the rejection webhook.",
    )


class SubmissionDecisionResponseSerializer(serializers.Serializer):
    detail = serializers.CharField(help_text="Confirmation of the applied decision.")
    submission = AdminSubmissionSerializer(help_text="The submission post-decision.")


class DemandSignalsSerializer(serializers.Serializer):
    """Admin-entered market-research signals for queue prioritisation."""

    demand_score = serializers.IntegerField(
        min_value=0,
        max_value=100,
        help_text="0-100 market-demand signal used to order the Recommendations queue.",
    )
    estimated_monthly_earnings = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=False,
        allow_null=True,
        help_text="Estimated monthly earnings figure, in platform currency.",
    )


class PayoutBypassSerializer(serializers.Serializer):
    payout_bypass = serializers.BooleanField(
        help_text=(
            "True marks this specific idea no-payout (creator will not be "
            "paid); False clears it. The developer is webhook-notified on "
            "every change."
        ),
    )

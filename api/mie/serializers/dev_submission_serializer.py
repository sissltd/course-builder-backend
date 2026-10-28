from drf_spectacular.utils import OpenApiExample, extend_schema_serializer
from rest_framework import serializers

from api.mie.enums import SubmissionStatus


@extend_schema_serializer(
    examples=[
        OpenApiExample(
            "Approved idea",
            value={
                "id": "0d1c7b2e-6f5a-4a3f-9a2b-1f4e8c9d0a11",
                "reference": "SCB-0d1c7b2e-A",
                "title": "Build a Production-Grade Rust Course",
                "status": "APPROVED",
                "category_id": "3f2b9c1e-5d4a-4e8f-9b7c-2a1d0e6f4c3b",
                "course_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
                "rejection_reason": None,
                "payout_bypass": False,
                "queued_at": "2026-08-23T09:00:00Z",
                "decided_at": "2026-08-24T15:30:00Z",
                "created_datetime": "2026-08-23T08:55:00Z",
            },
            response_only=True,
        )
    ]
)
class DevSubmissionSerializer(serializers.Serializer):
    """One row of the developer's own submission queue."""

    id = serializers.UUIDField(help_text="Immutable internal submission id.")
    reference = serializers.CharField(
        source="public_reference",
        help_text=(
            "Public reference whose suffix letter tracks current status "
            "(P pending, D dup-in-queue, E dup-existing, X previously-"
            "rejected, A approved, R rejected). Updates automatically."
        ),
    )
    title = serializers.CharField(help_text="The submitted idea title.")
    status = serializers.ChoiceField(
        choices=SubmissionStatus.choices,
        help_text="Current pipeline state. Every state appears here.",
    )
    category_id = serializers.UUIDField(
        allow_null=True,
        help_text="The platform category the idea was matched to, if any.",
    )
    course_id = serializers.UUIDField(
        source="stable_course_id",
        allow_null=True,
        help_text=(
            "The course's id, fixed when the idea is first approved and kept "
            "through every later decision; the pushed course carries it. "
            "Null until the idea is approved."
        ),
    )
    rejection_reason = serializers.CharField(
        source="rejection_reason.label",
        default=None,
        allow_null=True,
        help_text="Taxonomy reason attached on rejection, when one applies.",
    )
    payout_bypass = serializers.BooleanField(
        help_text=(
            "True when a superadmin marked this specific idea as no-payout "
            "(creator will not be paid for it)."
        )
    )
    queued_at = serializers.DateTimeField(
        help_text="When the idea most recently entered the review queue."
    )
    decided_at = serializers.DateTimeField(
        help_text="When the latest approve/reject decision was taken, if any."
    )
    created_datetime = serializers.DateTimeField(help_text="When the idea arrived.")

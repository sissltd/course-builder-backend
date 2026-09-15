from drf_spectacular.utils import OpenApiExample, extend_schema_serializer
from rest_framework import serializers

from api.mie.enums import SubmissionStatus
from api.mie.models.course_submission import CONFIDENCE_NOTE_MAX_LENGTH


@extend_schema_serializer(
    examples=[
        OpenApiExample(
            "Course idea",
            value={
                "title": "Build a Production-Grade Rust Course",
                "description": "Systems programming for backend engineers",
                "audience": "mid-level backend developers",
                "confidence_note": (
                    "620 backend job postings asked for Rust this month, up "
                    "28% on last month."
                ),
            },
            request_only=True,
        )
    ]
)
class SubmissionIngestSerializer(serializers.Serializer):
    """Endpoint 1 request body.

    Stored verbatim; `title` is the only required field and drives all
    three dedup checks. `confidence_note` is the one other key the
    platform reads - it is lifted into its own column for reviewers.
    Extra keys ride along untouched.
    """

    title = serializers.CharField(
        max_length=255,
        help_text=(
            "The idea's title. Dedup is title-based: a previously rejected "
            "title, an existing course title, or a title already awaiting "
            "review short-circuits immediately."
        ),
    )
    confidence_note = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=CONFIDENCE_NOTE_MAX_LENGTH,
        help_text=(
            "Optional evidence for the idea's demand - job-posting counts, "
            "search volume, community questions - shown to reviewers as its "
            f"own field. Plain text, up to {CONFIDENCE_NOTE_MAX_LENGTH} "
            "characters after trimming; when omitted it is stored empty."
        ),
    )


class SubmissionIngestResponseSerializer(serializers.Serializer):
    """What Endpoint 1 answers with.

    The reference is the developer-facing id whose last segment encodes
    current state (P pending, D dup-in-queue, E dup-existing, X
    previously-rejected). It mutates as the idea moves; keep it as the
    correlation key in webhook payloads.
    """

    id = serializers.UUIDField(help_text="Immutable internal submission id.")
    reference = serializers.CharField(
        source="public_reference",
        help_text=(
            "Public reference (SCB-xxxxxxxx-S) whose suffix letter tracks "
            "current status and updates on every transition."
        )
    )
    status = serializers.ChoiceField(
        choices=SubmissionStatus.choices,
        help_text="Pipeline state set at ingestion by the dedup engine.",
    )
    created_datetime = serializers.DateTimeField(
        help_text="When the idea was received."
    )

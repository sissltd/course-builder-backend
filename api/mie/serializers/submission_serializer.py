from drf_spectacular.utils import OpenApiExample, extend_schema_serializer
from rest_framework import serializers

from api.courses.enums import DifficultyLevel
from api.mie.enums import SubmissionStatus
from api.mie.models.course_submission import (
    CONFIDENCE_NOTE_MAX_LENGTH,
    DESCRIPTION_MAX_LENGTH,
)


@extend_schema_serializer(
    examples=[
        OpenApiExample(
            "Course idea",
            value={
                "title": "Build a Production-Grade Rust Course",
                "description": "Systems programming for backend engineers",
                "category": "Software Engineering",
                "difficulty_level": "ADVANCED",
                "searches_per_month": 23000,
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
    three dedup checks. The fields below it are the ones the platform also
    reads - each is lifted into its own column so the reviewer's queue can
    show, sort and filter on it. Every one is optional, and any other key
    rides along untouched.
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
    description = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=DESCRIPTION_MAX_LENGTH,
        help_text=(
            "What the idea covers, shown in the reviewer's Topic details "
            f"panel. Plain text, up to {DESCRIPTION_MAX_LENGTH} characters "
            "after trimming."
        ),
    )
    category = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text=(
            "Platform category this idea belongs to, by name or slug - "
            "'Software Engineering' or 'software-engineering'. Matching is "
            "case-insensitive and ignores archived categories. A value that "
            "matches nothing is not an error: the idea is filed without a "
            "category and your value stays in the stored payload."
        ),
    )
    difficulty_level = serializers.ChoiceField(
        required=False,
        allow_blank=True,
        choices=DifficultyLevel.choices,
        help_text=(
            "How hard the resulting course would be: "
            f"{', '.join(DifficultyLevel.values)}. Shown as the Difficulty "
            "level column and filterable there."
        ),
    )
    searches_per_month = serializers.IntegerField(
        required=False,
        min_value=0,
        help_text=(
            "Monthly search volume behind the idea, as a whole number. The "
            "reviewer's queue shows it beside the demand score, so send it "
            "when you have measured it."
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

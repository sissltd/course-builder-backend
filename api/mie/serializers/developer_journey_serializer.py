from rest_framework import serializers

from api.mie.enums import DeveloperJourneyStage


class DeveloperJourneySerializer(serializers.Serializer):
    """How far a developer account has got from registration to a published
    course (developer_journey_service). Worked out from the account, its
    ideas and their courses on every read; never set directly."""

    stage = serializers.ChoiceField(
        choices=DeveloperJourneyStage.choices,
        help_text=(
            "The furthest stage any of the account's ideas or courses has "
            "reached, in order: REGISTERED, ONBOARDED, IDEA_SUBMITTED, "
            "IDEA_APPROVED, COURSE_DELIVERED, TEXT_APPROVED, QA_PASSED, "
            "PUBLISHED."
        ),
    )
    label = serializers.CharField(help_text="The stage in words, for display.")
    step = serializers.IntegerField(help_text="The stage's position, from 1.")
    total_steps = serializers.IntegerField(help_text="How many stages there are.")
    next_step = serializers.CharField(
        help_text="What the developer does next to move on from this stage."
    )
    ideas_submitted = serializers.IntegerField(help_text="Ideas the account has sent.")
    ideas_approved = serializers.IntegerField(help_text="Of those, ideas approved now.")
    courses_delivered = serializers.IntegerField(
        help_text="Courses pushed for its ideas, archived ones included."
    )
    courses_published = serializers.IntegerField(help_text="Of those, courses published.")

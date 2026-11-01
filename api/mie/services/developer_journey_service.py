"""Where each developer account is on its way from registration to a
published course (DeveloperJourneyStage).

The stage is worked out from what is already stored - the account status,
its ideas and their courses - and never kept in a column of its own, so it
cannot drift from them. It is the furthest any of the account's ideas or
courses has reached: a developer with one published course and one idea
still in review is PUBLISHED.

`with_journey` adds the stage and its counts to a DeveloperAccount queryset
in the same grouped query, so a list costs the same however many accounts
it shows and can be filtered on `journey_stage`. `journey_payload` turns an
account into the `journey` block both the admin and the developer see.
"""

from django.db.models import Case, CharField, Count, Q, QuerySet, Value, When

from api.courses.enums import CourseStatus
from api.mie.enums import DeveloperAccountStatus, DeveloperJourneyStage, SubmissionStatus
from api.mie.models import DeveloperAccount

TEXT_APPROVED_STATUSES = (
    CourseStatus.AWAITING_VIDEO,
    CourseStatus.QA_VERIFICATION,
    CourseStatus.APPROVED,
    CourseStatus.PUBLISHED,
)
"""Course statuses past content review. QA_VERIFICATION is reached straight
from content review when the platform reviews text and video together."""

QA_PASSED_STATUSES = (CourseStatus.APPROVED, CourseStatus.PUBLISHED)

NEXT_STEPS = {
    DeveloperJourneyStage.REGISTERED: (
        "Wait for the platform to approve your registration. Your API key "
        "is issued then."
    ),
    DeveloperJourneyStage.ONBOARDED: (
        "Submit your first course idea with POST /mie/v1/submissions/."
    ),
    DeveloperJourneyStage.IDEA_SUBMITTED: (
        "Wait for SUBMISSION_APPROVED on one of your ideas, or submit more."
    ),
    DeveloperJourneyStage.IDEA_APPROVED: (
        "Write the course for your approved idea and deliver it with POST "
        "/mie/v1/submissions/{id}/course/ (Studio: a kind text delivery)."
    ),
    DeveloperJourneyStage.COURSE_DELIVERED: (
        "Your course is in content review. On COURSE_REVISION_REQUESTED, fix "
        "it and deliver it again; otherwise wait for COURSE_TEXT_APPROVED."
    ),
    DeveloperJourneyStage.TEXT_APPROVED: (
        "Send the video when it is yours to make (COURSE_TEXT_APPROVED, or "
        "COURSE_VIDEO_ASSIGNED with video_producer DEVELOPER). The course "
        "then goes to QA verification."
    ),
    DeveloperJourneyStage.QA_PASSED: (
        "A course passed QA verification, the point your plan pays at. "
        "Publication comes next."
    ),
    DeveloperJourneyStage.PUBLISHED: (
        "A course of yours is live. Keep submitting ideas."
    ),
}

COUNT_FIELDS = (
    "ideas_submitted",
    "ideas_approved",
    "courses_delivered",
    "courses_published",
)


def with_journey(queryset: QuerySet) -> QuerySet:
    """`queryset` (of DeveloperAccount) annotated with `journey_stage` and
    the counts behind it."""

    course = "submissions__resulting_course"
    return queryset.annotate(
        ideas_submitted=Count("submissions"),
        ideas_approved=Count(
            "submissions", filter=Q(submissions__status=SubmissionStatus.APPROVED)
        ),
        courses_delivered=Count(course),
        courses_text_approved=Count(
            course, filter=Q(**{f"{course}__status__in": TEXT_APPROVED_STATUSES})
        ),
        courses_passed_qa=Count(
            course, filter=Q(**{f"{course}__status__in": QA_PASSED_STATUSES})
        ),
        courses_published=Count(
            course, filter=Q(**{f"{course}__status": CourseStatus.PUBLISHED})
        ),
    ).annotate(
        journey_stage=Case(
            When(courses_published__gt=0, then=Value(DeveloperJourneyStage.PUBLISHED)),
            When(courses_passed_qa__gt=0, then=Value(DeveloperJourneyStage.QA_PASSED)),
            When(
                courses_text_approved__gt=0,
                then=Value(DeveloperJourneyStage.TEXT_APPROVED),
            ),
            When(
                courses_delivered__gt=0,
                then=Value(DeveloperJourneyStage.COURSE_DELIVERED),
            ),
            When(ideas_approved__gt=0, then=Value(DeveloperJourneyStage.IDEA_APPROVED)),
            When(ideas_submitted__gt=0, then=Value(DeveloperJourneyStage.IDEA_SUBMITTED)),
            # A suspended account was approved once; its journey stands.
            When(
                status__in=(
                    DeveloperAccountStatus.APPROVED,
                    DeveloperAccountStatus.SUSPENDED,
                ),
                then=Value(DeveloperJourneyStage.ONBOARDED),
            ),
            default=Value(DeveloperJourneyStage.REGISTERED),
            output_field=CharField(),
        )
    )


def journey_payload(*, account: DeveloperAccount) -> dict:
    """The DeveloperJourneySerializer shape for one account.

    Reads the `with_journey` annotations when `account` carries them, and
    otherwise works them out with one query, so an account fresh from an
    admin action is never shown a stage from before it.
    """

    if getattr(account, "journey_stage", None) is None:
        account = with_journey(DeveloperAccount.objects.filter(pk=account.pk)).get()
    stage = DeveloperJourneyStage(account.journey_stage)
    stages = list(DeveloperJourneyStage)
    return {
        "stage": stage.value,
        "label": stage.label,
        "step": stages.index(stage) + 1,
        "total_steps": len(stages),
        "next_step": NEXT_STEPS[stage],
        **{field: getattr(account, field) for field in COUNT_FIELDS},
    }

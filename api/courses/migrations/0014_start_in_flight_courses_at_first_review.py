"""Start every course already in content review at First Review.

Before the chain, content review was a single seat, so a Submitted or In
Review course is by definition waiting at, or held in, what is now First
Review. Each starts its cycle clean, as course_service.start_review_cycle
does for a new submission: four eyes reads every completed seat it finds as
part of the current cycle, and rows left over from an earlier pass
(QA's included) would otherwise lock the wrong people out.

A Verifier may no longer hold First Review, so their In Review claims go
back to pending rather than staying with someone who can't decide them. An
In Review course nobody holds goes back too - no reviewer could decide it.
"""

from django.db import migrations
from django.db.models import Q
from django.utils import timezone

IN_FLIGHT_STATUSES = ("SUBMITTED", "IN_REVIEW")

#: Who may hold First Review once the chain exists: Creator Reviewers and the
#: Admin tier, plus superusers. Frozen here rather than imported, so the
#: migration keeps meaning what it meant when it ran.
FIRST_REVIEW_ROLES = ("CREATOR_REVIEWER", "ADMIN", "STAFF_APPROVER", "SUPER_ADMIN")


def start_in_flight_courses_at_first_review(apps, schema_editor):
    Course = apps.get_model("courses", "Course")
    ReviewAssignment = apps.get_model("reviews", "ReviewAssignment")
    now = timezone.now()

    in_flight_ids = list(
        Course.objects.filter(status__in=IN_FLIGHT_STATUSES).values_list(
            "pk", flat=True
        )
    )
    kept_claims = ReviewAssignment.objects.filter(
        course__status="IN_REVIEW", stage="CONTENT"
    ).filter(Q(reviewer__role__in=FIRST_REVIEW_ROLES) | Q(reviewer__is_superuser=True))
    kept_claim_ids = list(kept_claims.values_list("pk", flat=True))
    held_course_ids = list(kept_claims.values_list("course_id", flat=True))

    Course.objects.filter(status="IN_REVIEW").exclude(pk__in=held_course_ids).update(
        status="SUBMITTED", updated_datetime=now
    )
    ReviewAssignment.objects.filter(course_id__in=in_flight_ids).exclude(
        pk__in=kept_claim_ids
    ).update(reviewer=None, claimed_at=None, completed_at=None, updated_datetime=now)
    ReviewAssignment.objects.filter(pk__in=kept_claim_ids).update(
        completed_at=None, updated_datetime=now
    )
    Course.objects.filter(pk__in=in_flight_ids).update(review_stage="CONTENT")


def clear_review_stage(apps, schema_editor):
    """Reverse: the single-seat flow has no seats, so blank the column again.

    Released claims stay released and cleared stamps stay cleared. A
    Submitted course with no claimant is a state the single-seat flow
    accepts, and handing a course back to a Verifier would be guessing.
    """

    apps.get_model("courses", "Course").objects.exclude(review_stage="").update(
        review_stage=""
    )


class Migration(migrations.Migration):
    dependencies = [
        ("courses", "0013_course_review_stage"),
        ("reviews", "0006_widen_review_stage"),
        # The claim release reads User.role.
        ("users", "0003_user_role"),
    ]

    operations = [
        migrations.RunPython(
            start_in_flight_courses_at_first_review,
            reverse_code=clear_review_stage,
        ),
    ]

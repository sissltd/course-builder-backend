import pytest

from api.courses.models import Course
from api.courses.tests.factories import make_user
from api.mie.enums import SubmissionStatus, WebhookEventType
from api.mie.models import WebhookEvent
from api.mie.services.submission_admin_service import (
    decide_submission,
    decide_submissions_bulk,
)
from api.mie.tests.factories import (
    make_approved_developer,
    make_submission,
    make_rejection_reason,
)
from api.users.enums import UserRole


@pytest.mark.django_db
def test_identity_is_issued_only_at_approval_and_stays_stable_after_reversal():
    actor = make_user(role=UserRole.SUPER_ADMIN)
    developer, _ = make_approved_developer()
    idea = make_submission(developer=developer)
    assert idea.reserved_course_id is None
    before = Course.objects.count()
    approved = decide_submission(actor=actor, submission=idea, approve=True)
    issued = approved.reserved_course_id
    assert issued is not None
    assert Course.objects.count() == before
    event = WebhookEvent.objects.filter(
        event_type=WebhookEventType.SUBMISSION_APPROVED
    ).latest("created_datetime")
    assert event.payload["submission"]["id"] == str(idea.id)
    assert event.payload["submission"]["course"]["id"] == str(issued)
    rejected = decide_submission(
        actor=actor,
        submission=approved,
        approve=False,
        rejection_reason=make_rejection_reason(),
    )
    assert rejected.status == SubmissionStatus.REJECTED
    assert rejected.reserved_course_id == issued
    # Reusing the original stale instance must not generate a second identity.
    again = decide_submission(actor=actor, submission=idea, approve=True)
    assert again.reserved_course_id == issued
    assert Course.objects.count() == before


@pytest.mark.django_db
def test_bulk_approval_issues_distinct_ids_without_creating_courses():
    actor = make_user(role=UserRole.SUPER_ADMIN)
    developer, _ = make_approved_developer()
    ideas = [make_submission(developer=developer) for _ in range(3)]
    before = Course.objects.count()
    approved = decide_submissions_bulk(
        actor=actor, submission_ids=[x.id for x in ideas], approve=True
    )
    ids = {x.reserved_course_id for x in approved}
    assert None not in ids
    assert len(ids) == len(ideas)
    assert Course.objects.count() == before
    repeated = decide_submissions_bulk(
        actor=actor, submission_ids=[x.id for x in ideas], approve=True
    )
    assert {x.reserved_course_id for x in repeated} == ids

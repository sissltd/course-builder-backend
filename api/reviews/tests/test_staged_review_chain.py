"""The review chain with the staged review flow on.

Text is reviewed first (First Review), the video is attached after it passes
and reviewed at the Second Review seat, then Verification, QA and the
Approver. A rejection at any seat parks the course at Needs Revision and a
resubmission resumes at that seat. Everything goes through the URLs the
frontend calls. With the switch off the original chain is unchanged (the
existing review tests cover it); the off cases here pin the differences.
"""

from types import SimpleNamespace
from unittest import mock

import pytest
from django.utils import timezone

from api.courses.enums import AppealStatus, CourseStatus, VideoProvider
from api.courses.models import Course, CourseVersion
from api.courses.services import course_service
from api.courses.tests.factories import (
    build_compliant_course,
    make_course_appeal,
)
from api.notification.models import Notification
from api.platform.services import platform_settings_service
from api.reviews.enums import ReviewActionType, ReviewStage
from api.reviews.models import ReviewAction, ReviewAssignment
from api.users.enums import UserRole

QUEUE = "/api/v1/review-queue/"
PREVIEW_URL = "https://example.com/preview.mp4"
PRICES = {
    "distribution_channels": [
        {"channel": "SOLUDESK", "learner_price": "149.00", "model": "ONE_TIME"}
    ]
}


@pytest.fixture
def staged_flow(db):
    platform_settings_service.update_settings(staged_review_flow_enabled=True)


@pytest.fixture
def people(make_user):
    CourseVersion.objects.get_or_create(label="1.0")
    return SimpleNamespace(
        creator=make_user(role=UserRole.COURSE_CREATOR),
        writer=make_user(role=UserRole.STAFF_WRITER),
        other_writer=make_user(role=UserRole.STAFF_WRITER),
        creator_reviewer=make_user(role=UserRole.CREATOR_REVIEWER),
        verifier=make_user(role=UserRole.STAFF_VERIFIER),
        qa=make_user(role=UserRole.QA_REVIEWER),
        approver=make_user(role=UserRole.STAFF_APPROVER),
        admin=make_user(role=UserRole.ADMIN),
    )


def _text_course(people, **fields):
    course = build_compliant_course(creator=people.creator)
    course.preview_video_url = ""
    for name, value in fields.items():
        setattr(course, name, value)
    course.save()
    return course


def _submitted_text_course(people):
    return course_service.submit_course(
        course=_text_course(people), actor=people.creator
    )


def _course_at(people, seat):
    """A course with its video attached, waiting at `seat` with every earlier
    seat decided by a different reviewer."""

    course = _text_course(
        people,
        preview_video_url=PREVIEW_URL,
        video_attached_at=timezone.now(),
        video_provider=VideoProvider.CREATOR,
        status=CourseStatus.SUBMITTED,
        review_stage=seat,
    )
    earlier = {
        ReviewStage.CONTENT: [],
        ReviewStage.SECOND_REVIEW: [(ReviewStage.CONTENT, people.writer)],
        ReviewStage.VERIFICATION: [
            (ReviewStage.CONTENT, people.writer),
            (ReviewStage.SECOND_REVIEW, people.other_writer),
        ],
    }[seat]
    for stage, reviewer in earlier:
        ReviewAssignment.objects.create(
            course=course,
            stage=stage,
            reviewer=reviewer,
            claimed_at=timezone.now(),
            completed_at=timezone.now(),
        )
    return course


def _post(client, user, path, body=None):
    client.force_authenticate(user)
    return client.post(path, body or {}, format="json")


def _claim(client, course, reviewer):
    return _post(client, reviewer, f"{QUEUE}{course.id}/claim/")


def _approve(client, course, reviewer):
    return _post(client, reviewer, f"{QUEUE}{course.id}/approve/")


def _reject(client, course, reviewer, summary="Please fix this"):
    return _post(
        client,
        reviewer,
        f"{QUEUE}{course.id}/reject/",
        {"feedback": {"summary": summary}},
    )


def _take_seat(client, course, reviewer):
    assert _claim(client, course, reviewer).status_code == 200
    response = _approve(client, course, reviewer)
    assert response.status_code == 200
    course.refresh_from_db()
    return response


# --- First Review: the text only, then wait for the video --------------------


@pytest.mark.django_db
def test_approving_the_text_parks_the_course_until_its_video_arrives(
    api_client, people, staged_flow
):
    course = _submitted_text_course(people)

    _take_seat(api_client, course, people.writer)

    assert course.status == CourseStatus.AWAITING_VIDEO
    assert course.review_stage == ""
    assert course.video_provider == ""  # the creator has not chosen yet
    approved = ReviewAction.objects.get(course=course, action=ReviewActionType.APPROVE)
    assert approved.stage == ReviewStage.CONTENT


@pytest.mark.django_db
def test_the_creator_is_asked_whether_they_will_add_the_video(
    api_client, people, staged_flow
):
    course = _submitted_text_course(people)

    _take_seat(api_client, course, people.writer)

    asked = Notification.objects.get(
        receiver=people.creator, title="Text approved - add your video?"
    )
    assert asked.metadata["course_id"] == str(course.id)
    assert asked.metadata["action"] == "video_decision"


@pytest.mark.django_db
def test_a_course_runs_from_text_review_to_qa_with_the_video_in_between(
    api_client, people, staged_flow
):
    course = _submitted_text_course(people)
    _take_seat(api_client, course, people.writer)  # First Review: text

    course.preview_video_url = PREVIEW_URL
    course.save()
    decided = _post(
        api_client,
        people.creator,
        f"/api/v1/courses/{course.id}/video-decision/",
        {"will_provide": True},
    )
    submitted = _post(
        api_client, people.creator, f"/api/v1/courses/{course.id}/submit-video/"
    )
    assert decided.status_code == 200
    assert submitted.status_code == 200
    course.refresh_from_db()
    assert (course.status, course.review_stage) == (
        CourseStatus.SUBMITTED,
        ReviewStage.SECOND_REVIEW,
    )

    _take_seat(api_client, course, people.other_writer)  # Second Review: video
    assert course.review_stage == ReviewStage.VERIFICATION
    _take_seat(api_client, course, people.verifier)
    assert course.status == CourseStatus.QA_VERIFICATION


# --- who may sit the seats ----------------------------------------------------


@pytest.mark.django_db
def test_on_a_writer_may_claim_the_first_seat_and_a_creator_reviewer_cannot_even_see_it(
    api_client, people, staged_flow
):
    course = _submitted_text_course(people)

    refused = _claim(api_client, course, people.creator_reviewer)
    allowed = _claim(api_client, course, people.writer)

    assert refused.status_code == 404  # not at their seat, so not theirs to see
    assert allowed.status_code == 200


@pytest.mark.django_db
def test_off_a_creator_reviewer_still_claims_the_first_seat_and_a_writer_may_not(
    api_client, people
):
    course = build_compliant_course(creator=people.creator)
    course = course_service.submit_course(course=course, actor=people.creator)

    allowed = _claim(api_client, course, people.creator_reviewer)
    refused = _claim(api_client, course, people.writer)

    assert allowed.status_code == 200
    assert refused.status_code == 403


@pytest.mark.django_db
def test_four_eyes_still_stops_the_first_seat_reviewer_taking_the_second(
    api_client, people, staged_flow
):
    course = _course_at(people, ReviewStage.SECOND_REVIEW)

    # `writer` decided the first seat in _course_at.
    response = _claim(api_client, course, people.writer)

    assert response.status_code == 403
    assert "decided an earlier seat" in response.json()["errors"][0]["message"]


# --- rejection parks the course and remembers the seat -----------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    "seat, reviewer_attr",
    [
        (ReviewStage.CONTENT, "writer"),
        (ReviewStage.SECOND_REVIEW, "other_writer"),
        (ReviewStage.VERIFICATION, "verifier"),
    ],
)
def test_a_rejection_at_any_seat_returns_the_course_for_revision(
    api_client, people, staged_flow, seat, reviewer_attr
):
    course = _course_at(people, seat)
    reviewer = getattr(people, reviewer_attr)
    assert _claim(api_client, course, reviewer).status_code == 200

    response = _reject(api_client, course, reviewer)

    assert response.status_code == 200
    course.refresh_from_db()
    assert course.status == CourseStatus.NEEDS_REVISION
    assert course.revision_seat == seat
    assert course.review_stage == ""
    assert course.rejected_at is not None
    assert "Needs Revision" in Notification.objects.get(
        receiver=people.creator, title="Course rejected"
    ).content


@pytest.mark.django_db
def test_off_a_rejection_still_returns_the_course_to_draft(api_client, people):
    course = build_compliant_course(creator=people.creator)
    course = course_service.submit_course(course=course, actor=people.creator)
    assert _claim(api_client, course, people.creator_reviewer).status_code == 200

    assert _reject(api_client, course, people.creator_reviewer).status_code == 200

    course.refresh_from_db()
    assert course.status == CourseStatus.DRAFT
    assert course.revision_seat == ""


# --- resubmitting resumes at the rejecting seat ------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    "seat, reviewer_attr",
    [
        (ReviewStage.CONTENT, "writer"),
        (ReviewStage.SECOND_REVIEW, "other_writer"),
        (ReviewStage.VERIFICATION, "verifier"),
    ],
)
def test_resubmitting_resumes_at_the_rejecting_seat_and_keeps_earlier_decisions(
    api_client, people, staged_flow, seat, reviewer_attr
):
    course = _course_at(people, seat)
    if seat == ReviewStage.CONTENT:
        course.preview_video_url = ""
        course.video_attached_at = None
        course.save()
    reviewer = getattr(people, reviewer_attr)
    _claim(api_client, course, reviewer)
    _reject(api_client, course, reviewer)
    earlier_before = {
        row.stage: (row.reviewer_id, row.completed_at)
        for row in ReviewAssignment.objects.filter(course=course).exclude(stage=seat)
    }

    response = _post(api_client, people.creator, f"/api/v1/courses/{course.id}/submit/")

    assert response.status_code == 200
    course.refresh_from_db()
    assert (course.status, course.review_stage) == (CourseStatus.SUBMITTED, seat)
    assert course.revision_seat == ""
    seat_row = ReviewAssignment.objects.get(course=course, stage=seat)
    assert seat_row.reviewer_id is None and seat_row.completed_at is None
    earlier_after = {
        row.stage: (row.reviewer_id, row.completed_at)
        for row in ReviewAssignment.objects.filter(course=course).exclude(stage=seat)
    }
    assert earlier_after == earlier_before  # no earlier seat reviews it again


@pytest.mark.django_db
def test_the_rejecting_reviewer_may_take_the_seat_again_after_the_fix(
    api_client, people, staged_flow
):
    course = _course_at(people, ReviewStage.VERIFICATION)
    _claim(api_client, course, people.verifier)
    _reject(api_client, course, people.verifier)
    _post(api_client, people.creator, f"/api/v1/courses/{course.id}/submit/")

    assert _claim(api_client, course, people.verifier).status_code == 200


@pytest.mark.django_db
def test_a_resubmission_is_checked_again_and_stays_parked_if_it_fails(
    api_client, people, staged_flow
):
    course = _course_at(people, ReviewStage.VERIFICATION)
    _claim(api_client, course, people.verifier)
    _reject(api_client, course, people.verifier)
    course.refresh_from_db()
    course.modules.first().assessment.delete()  # break a text rule

    response = _post(api_client, people.creator, f"/api/v1/courses/{course.id}/submit/")

    assert response.status_code == 400
    assert response.json()["errors"][0]["field_name"] == "structural_standards"
    course.refresh_from_db()
    assert course.status == CourseStatus.NEEDS_REVISION
    assert course.revision_seat == ReviewStage.VERIFICATION


@pytest.mark.django_db
def test_only_the_owner_can_resubmit(api_client, people, staged_flow, make_user):
    course = _course_at(people, ReviewStage.VERIFICATION)
    _claim(api_client, course, people.verifier)
    _reject(api_client, course, people.verifier)

    response = _post(
        api_client,
        make_user(role=UserRole.COURSE_CREATOR),
        f"/api/v1/courses/{course.id}/submit/",
    )

    assert response.status_code in (403, 404)
    course.refresh_from_db()
    assert course.status == CourseStatus.NEEDS_REVISION


# --- QA -----------------------------------------------------------------------


def _course_in_qa(people):
    return _text_course(
        people,
        preview_video_url=PREVIEW_URL,
        video_attached_at=timezone.now(),
        video_provider=VideoProvider.CREATOR,
        status=CourseStatus.QA_VERIFICATION,
    )


@pytest.mark.django_db
def test_a_qa_rejection_records_the_decision_and_resumes_at_qa(
    api_client, people, staged_flow
):
    course = _course_in_qa(people)

    rejected = _post(
        api_client,
        people.qa,
        f"{QUEUE}{course.id}/qa-reject/",
        {"feedback": {"summary": "Audio is too quiet"}},
    )

    assert rejected.status_code == 200
    course.refresh_from_db()
    assert (course.status, course.revision_seat) == (
        CourseStatus.NEEDS_REVISION,
        ReviewStage.QA,
    )
    qa_row = ReviewAssignment.objects.get(course=course, stage=ReviewStage.QA)
    assert qa_row.reviewer_id == people.qa.id
    assert qa_row.completed_at is not None  # the decision is recorded

    resubmitted = _post(
        api_client, people.creator, f"/api/v1/courses/{course.id}/submit/"
    )

    assert resubmitted.status_code == 200
    course.refresh_from_db()
    assert course.status == CourseStatus.QA_VERIFICATION
    assert course.review_stage == ""
    qa_row.refresh_from_db()
    assert qa_row.reviewer_id is None and qa_row.completed_at is None


# --- the Approver prices and publishes ---------------------------------------


def _approved(people):
    course = _text_course(
        people,
        preview_video_url=PREVIEW_URL,
        video_attached_at=timezone.now(),
        status=CourseStatus.APPROVED,
        creator_price_snapshot="150.00",
    )
    return course


@pytest.mark.django_db
@pytest.mark.parametrize("who", ["creator_reviewer", "verifier", "admin"])
def test_on_only_the_approver_can_publish_through_the_reviewer_route(
    api_client, people, staged_flow, who
):
    course = _approved(people)

    response = _post(api_client, getattr(people, who), f"{QUEUE}{course.id}/publish/")

    # An approved course is at the Approver's seat: nobody else can see it.
    assert response.status_code == 404
    course.refresh_from_db()
    assert course.status == CourseStatus.APPROVED


@pytest.mark.django_db
@pytest.mark.parametrize("who", ["creator_reviewer", "verifier", "admin", "writer"])
def test_the_service_itself_refuses_anyone_but_the_approver_to_publish(
    people, staged_flow, who
):
    """Defence in depth: the views hide the course, but publish_course is
    also called from elsewhere, so it checks the Approver on its own."""

    from rest_framework import exceptions

    course = _approved(people)

    with pytest.raises(exceptions.PermissionDenied):
        course_service.publish_course(course=course, actor=getattr(people, who))
    course.refresh_from_db()
    assert course.status == CourseStatus.APPROVED


@pytest.mark.django_db
def test_on_the_approver_publishes(api_client, people, staged_flow):
    course = _approved(people)

    response = _post(api_client, people.approver, f"{QUEUE}{course.id}/publish/")

    assert response.status_code == 200
    course.refresh_from_db()
    assert course.status == CourseStatus.PUBLISHED


@pytest.mark.django_db
def test_on_an_admin_cannot_reach_the_course_through_the_creator_side_publish_route(
    api_client, people, staged_flow
):
    course = _approved(people)

    response = _post(
        api_client, people.admin, f"/api/v1/courses/{course.id}/publish/", PRICES
    )

    assert response.status_code == 404
    course.refresh_from_db()
    assert course.status == CourseStatus.APPROVED


@pytest.mark.django_db
def test_on_only_the_approver_can_set_prices(api_client, people, staged_flow):
    course = _approved(people)
    url = f"{QUEUE}{course.id}/review-prices/"
    api_client.force_authenticate(people.creator_reviewer)
    refused = api_client.put(url, PRICES, format="json")
    api_client.force_authenticate(people.approver)
    allowed = api_client.put(url, PRICES, format="json")

    assert refused.status_code == 404
    assert allowed.status_code == 200
    assert course.distribution_channels.count() == 1


@pytest.mark.django_db
def test_off_a_creator_reviewer_can_still_price_and_publish(api_client, people):
    course = _approved(people)
    api_client.force_authenticate(people.creator_reviewer)

    priced = api_client.put(
        f"{QUEUE}{course.id}/review-prices/", PRICES, format="json"
    )
    published = api_client.post(f"{QUEUE}{course.id}/publish/", {}, format="json")

    assert priced.status_code == 200
    assert published.status_code == 200


@pytest.mark.django_db
def test_publishing_hands_the_course_to_final_production(
    api_client, people, staged_flow
):
    course = _approved(people)

    with mock.patch(
        "api.courses.services.production_engine.finalize"
    ) as finalize:
        response = _post(api_client, people.approver, f"{QUEUE}{course.id}/publish/")

    assert response.status_code == 200
    finalize.assert_called_once()
    assert finalize.call_args.kwargs["course"].id == course.id
    assert finalize.call_args.kwargs["actor"].id == people.approver.id


# --- appeals ------------------------------------------------------------------


@pytest.mark.django_db
def test_an_approved_appeal_resumes_at_the_rejecting_seat(
    api_client, people, staged_flow
):
    course = _course_at(people, ReviewStage.VERIFICATION)
    _claim(api_client, course, people.verifier)
    _reject(api_client, course, people.verifier)
    appeal = make_course_appeal(course=course, submitted_by=people.creator)

    response = _post(
        api_client, people.admin, f"/api/v1/course-appeals/{appeal.id}/approve/"
    )

    assert response.status_code == 200
    appeal.refresh_from_db()
    assert appeal.status == AppealStatus.APPROVED
    course.refresh_from_db()
    assert (course.status, course.review_stage) == (
        CourseStatus.SUBMITTED,
        ReviewStage.VERIFICATION,
    )
    kept = ReviewAssignment.objects.filter(
        course=course, stage__in=[ReviewStage.CONTENT, ReviewStage.SECOND_REVIEW]
    )
    assert kept.count() == 2 and all(row.completed_at for row in kept)


@pytest.mark.django_db
def test_a_creator_can_appeal_a_course_awaiting_revision(
    api_client, people, staged_flow
):
    course = _course_at(people, ReviewStage.SECOND_REVIEW)
    _claim(api_client, course, people.other_writer)
    _reject(api_client, course, people.other_writer)

    response = _post(
        api_client,
        people.creator,
        "/api/v1/course-appeals/",
        {
            "course": str(course.id),
            "title": "Unfair rejection",
            "email": "creator@example.com",
            "description": "Please reconsider.",
        },
    )

    assert response.status_code == 201


@pytest.mark.django_db
def test_an_unrejected_in_flight_course_cannot_be_appealed(
    api_client, people, staged_flow
):
    course = _course_at(people, ReviewStage.SECOND_REVIEW)

    response = _post(
        api_client,
        people.creator,
        "/api/v1/course-appeals/",
        {
            "course": str(course.id),
            "title": "Unfair rejection",
            "email": "creator@example.com",
            "description": "Please reconsider.",
        },
    )

    assert response.status_code == 400
    assert Course.objects.get(pk=course.pk).status == CourseStatus.SUBMITTED


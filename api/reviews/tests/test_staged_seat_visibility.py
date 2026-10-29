"""With the staged review flow on, a course is visible only to the role sitting
its current seat, and to the Super Admin.

The seat moves with the course (First Review, video review, Verification, QA,
Approver), so a course appears for the next role only once it leaves the
previous one. Everything goes through the URLs the frontend calls. With the
flow off nothing is hidden, as before.
"""

from types import SimpleNamespace

import pytest

from api.courses.enums import CourseStatus
from api.courses.tests.factories import make_category, make_draft_course
from api.platform.services import platform_settings_service
from api.reviews.enums import ReviewStage
from api.users.enums import UserRole

QUEUE = "/api/v1/review-queue/"
ADMIN_COURSES = "/api/v1/admin/courses/"

#: Where each test course sits. Keys name the seat so the expectations below
#: read as a table.
PLACEMENTS = {
    "first_seat": dict(status=CourseStatus.SUBMITTED, review_stage=ReviewStage.CONTENT),
    "first_seat_claimed": dict(
        status=CourseStatus.IN_REVIEW, review_stage=ReviewStage.CONTENT
    ),
    "video_seat": dict(
        status=CourseStatus.SUBMITTED, review_stage=ReviewStage.SECOND_REVIEW
    ),
    "verification_seat": dict(
        status=CourseStatus.SUBMITTED, review_stage=ReviewStage.VERIFICATION
    ),
    "qa": dict(status=CourseStatus.QA_VERIFICATION),
    "approver": dict(status=CourseStatus.APPROVED),
    "revision_from_first": dict(
        status=CourseStatus.NEEDS_REVISION, revision_seat=ReviewStage.CONTENT
    ),
    "revision_from_verification": dict(
        status=CourseStatus.NEEDS_REVISION, revision_seat=ReviewStage.VERIFICATION
    ),
    "revision_from_qa": dict(
        status=CourseStatus.NEEDS_REVISION, revision_seat=ReviewStage.QA
    ),
    "awaiting_video": dict(status=CourseStatus.AWAITING_VIDEO),
    "draft": dict(status=CourseStatus.DRAFT),
    "published": dict(status=CourseStatus.PUBLISHED),
}

#: What each role sees with the flow on. Published courses are nobody's seat
#: and stay visible. Creator Reviewer and Admin sit no staged seat.
SEES = {
    UserRole.STAFF_WRITER: {
        "first_seat",
        "first_seat_claimed",
        "video_seat",
        "revision_from_first",
        "published",
    },
    UserRole.STAFF_VERIFIER: {
        "verification_seat",
        "revision_from_verification",
        "published",
    },
    UserRole.QA_REVIEWER: {"qa", "revision_from_qa", "published"},
    UserRole.STAFF_APPROVER: {"approver", "published"},
    UserRole.CREATOR_REVIEWER: {"published"},
    UserRole.ADMIN: {"published"},
    UserRole.SUPER_ADMIN: set(PLACEMENTS),
}


@pytest.fixture
def staged_flow(db):
    # Set directly: the courses are built first, and the API guard (tested in
    # api.platform) refuses to switch the flow while any is in review.
    row = platform_settings_service.get_settings()
    row.staged_review_flow_enabled = True
    row.save(update_fields=["staged_review_flow_enabled"])


@pytest.fixture
def courses(make_user):
    creator = make_user(role=UserRole.COURSE_CREATOR)
    category = make_category()
    return SimpleNamespace(
        **{
            key: make_draft_course(creator=creator, category=category, **placement)
            for key, placement in PLACEMENTS.items()
        }
    )


def _ids(response) -> set[str]:
    data = response.data
    if isinstance(data, dict) and "data" in data:
        data = data["data"]
    if isinstance(data, dict) and "results" in data:
        data = data["results"]
    return {str(row["id"]) for row in data}


def _keys(courses, ids: set[str]) -> set[str]:
    return {key for key in PLACEMENTS if str(getattr(courses, key).id) in ids}


@pytest.mark.django_db
@pytest.mark.parametrize("role", list(SEES))
def test_each_role_lists_only_the_courses_at_its_seat(
    api_client, make_user, courses, staged_flow, role
):
    api_client.force_authenticate(make_user(role=role))

    response = api_client.get(ADMIN_COURSES, {"size": 50})

    assert response.status_code == 200
    assert _keys(courses, _ids(response)) == SEES[role]


@pytest.mark.django_db
@pytest.mark.parametrize("role", list(SEES))
def test_each_role_opens_only_the_courses_at_its_seat(
    api_client, make_user, courses, staged_flow, role
):
    api_client.force_authenticate(make_user(role=role))

    opened = {
        key
        for key in PLACEMENTS
        if api_client.get(f"{QUEUE}{getattr(courses, key).id}/").status_code == 200
    }

    assert opened == SEES[role]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "role, queue_keys",
    [
        (
            UserRole.STAFF_WRITER,
            {"first_seat", "first_seat_claimed", "video_seat", "published"},
        ),
        (UserRole.STAFF_VERIFIER, {"verification_seat", "published"}),
        (UserRole.QA_REVIEWER, {"qa", "published"}),
        (UserRole.STAFF_APPROVER, {"approver", "published"}),
        (UserRole.CREATOR_REVIEWER, {"published"}),
        (UserRole.ADMIN, {"published"}),
    ],
)
def test_the_review_queue_list_follows_the_seat(
    api_client, make_user, courses, staged_flow, role, queue_keys
):
    api_client.force_authenticate(make_user(role=role))

    response = api_client.get(QUEUE)

    assert response.status_code == 200
    assert _keys(courses, _ids(response)) == queue_keys


@pytest.mark.django_db
def test_the_super_admin_sees_every_course_in_the_queue_statuses(
    api_client, make_user, courses, staged_flow
):
    api_client.force_authenticate(make_user(role=UserRole.SUPER_ADMIN))

    response = api_client.get(QUEUE)

    assert _keys(courses, _ids(response)) == {
        "first_seat",
        "first_seat_claimed",
        "video_seat",
        "verification_seat",
        "qa",
        "approver",
        "published",
    }


@pytest.mark.django_db
def test_a_course_moves_into_view_as_it_reaches_each_seat(
    api_client, make_user, courses, staged_flow
):
    """The same course is invisible to the Verifier at First Review and
    visible once it is at Verification."""

    verifier = make_user(role=UserRole.STAFF_VERIFIER)
    course = courses.first_seat
    api_client.force_authenticate(verifier)
    assert api_client.get(f"{QUEUE}{course.id}/").status_code == 404

    course.review_stage = ReviewStage.VERIFICATION
    course.save()

    assert api_client.get(f"{QUEUE}{course.id}/").status_code == 200


@pytest.mark.django_db
def test_a_hidden_course_cannot_be_claimed_or_decided_and_is_a_404(
    api_client, make_user, courses, staged_flow
):
    verifier = make_user(role=UserRole.STAFF_VERIFIER)
    api_client.force_authenticate(verifier)
    course = courses.first_seat

    claim = api_client.post(f"{QUEUE}{course.id}/claim/")
    approve = api_client.post(f"{QUEUE}{course.id}/approve/")
    reject = api_client.post(
        f"{QUEUE}{course.id}/reject/", {"feedback": {"summary": "no"}}, format="json"
    )

    assert (claim.status_code, approve.status_code, reject.status_code) == (404, 404, 404)
    course.refresh_from_db()
    assert course.status == CourseStatus.SUBMITTED


@pytest.mark.django_db
def test_the_admin_cannot_assign_a_seat_on_a_course_they_cannot_see(
    api_client, make_user, courses, staged_flow
):
    api_client.force_authenticate(make_user(role=UserRole.ADMIN))

    response = api_client.get(f"{ADMIN_COURSES}{courses.first_seat.id}/")

    assert response.status_code == 404


@pytest.mark.django_db
def test_the_creator_side_course_routes_do_not_leak_the_course(
    api_client, make_user, courses, staged_flow
):
    """/courses/{id}/ lets View/Edit Course holders open any course, so it
    needs the same rule."""

    api_client.force_authenticate(make_user(role=UserRole.ADMIN))

    hidden = api_client.get(f"/api/v1/courses/{courses.first_seat.id}/")
    published = api_client.get(f"/api/v1/courses/{courses.published.id}/")

    assert hidden.status_code == 404
    assert published.status_code == 200


@pytest.mark.django_db
def test_search_finds_only_the_courses_the_caller_may_see(
    api_client, make_user, courses, staged_flow
):
    for key in PLACEMENTS:
        course = getattr(courses, key)
        course.title = f"Seat probe {key}"
        course.save()

    def found(role):
        api_client.force_authenticate(make_user(role=role))
        response = api_client.get("/api/v1/global-search/", {"q": "Seat probe", "limit": 10})
        assert response.status_code == 200
        bucket = response.data["results"].get("courses", {"results": []})
        return {item["id"] for item in bucket["results"]}

    # limit caps each bucket, so compare against what the caller may see.
    verifier_ids = found(UserRole.STAFF_VERIFIER)
    assert str(courses.verification_seat.id) in verifier_ids
    assert str(courses.first_seat.id) not in verifier_ids
    assert str(courses.qa.id) not in verifier_ids

    writer_ids = found(UserRole.STAFF_WRITER)
    assert str(courses.first_seat.id) in writer_ids
    assert str(courses.verification_seat.id) not in writer_ids

    admin_ids = found(UserRole.ADMIN)
    assert str(courses.first_seat.id) not in admin_ids
    assert str(courses.published.id) in admin_ids


@pytest.mark.django_db
def test_the_dashboard_counts_agree_with_what_the_reviewer_can_see(
    api_client, make_user, courses, staged_flow
):
    api_client.force_authenticate(make_user(role=UserRole.STAFF_VERIFIER))
    courses.verification_seat.status = CourseStatus.IN_REVIEW
    courses.verification_seat.save()
    make_draft_course(
        creator=courses.first_seat.creator,
        category=courses.first_seat.category,
        status=CourseStatus.SUBMITTED,
        review_stage=ReviewStage.VERIFICATION,
    )

    response = api_client.get("/api/v1/reviewer/overview/")

    assert response.status_code == 200
    # Only the two Verification-seat courses count; the First Review, video
    # and later-seat courses in the same statuses are not theirs to see.
    assert response.data["queue"]["SUBMITTED"] == 1
    assert response.data["queue"]["IN_REVIEW"] == 1


# --- the flow off: nothing is hidden ------------------------------------------


@pytest.mark.django_db
def test_off_every_reviewer_still_sees_every_course_in_the_queue(
    api_client, make_user, courses
):
    api_client.force_authenticate(make_user(role=UserRole.STAFF_VERIFIER))

    response = api_client.get(QUEUE)

    assert _keys(courses, _ids(response)) == {
        "first_seat",
        "first_seat_claimed",
        "video_seat",
        "verification_seat",
        "qa",
        "approver",
        "published",
    }

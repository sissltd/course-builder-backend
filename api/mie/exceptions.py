from rest_framework import status
from rest_framework.exceptions import APIException


class IdeaNotApproved(APIException):
    """A course was pushed for an idea that is not APPROVED right now.

    409 rather than 400: the body may be perfectly valid; it conflicts with
    the idea's current state, which only an admin decision can change.
    """

    status_code = status.HTTP_409_CONFLICT
    default_code = "idea_not_approved"
    default_detail = "A course can only be pushed for an approved idea."


class CourseAlreadyInFlight(APIException):
    """A course was pushed for an idea whose course is past DRAFT.

    Once submitted, a course belongs to the review flow; it can only be
    replaced after a reviewer sends it back to DRAFT.
    """

    status_code = status.HTTP_409_CONFLICT
    default_code = "course_in_review"
    default_detail = (
        "This idea's course is already in review or published. It can only "
        "be replaced after a reviewer sends it back to DRAFT."
    )

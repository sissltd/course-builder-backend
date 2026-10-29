from rest_framework import status
from rest_framework.exceptions import APIException


class StagedReviewFlowInFlight(APIException):
    """Raised when the staged review flow is switched on or off while
    courses are mid-review.

    409 rather than 400 because the request is well-formed - it conflicts
    with the current state of the platform. The two flows read the same
    review seats differently (the second seat is a video review only with
    the switch on), so a course in flight would change meaning under its
    reviewers. The caller resolves it by waiting for the queue to drain.
    """

    status_code = status.HTTP_409_CONFLICT
    default_code = "staged_review_flow_in_flight"
    default_detail = (
        "The review flow cannot be switched while courses are in review, "
        "awaiting video, awaiting revision or approved but unpublished. "
        "Wait for them to finish or be returned to Draft."
    )

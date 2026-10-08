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


class WebhookEndpointLimitReached(APIException):
    """A developer tried to add an endpoint beyond the per-account limit."""

    status_code = status.HTTP_409_CONFLICT
    default_code = "webhook_endpoint_limit"
    default_detail = "You already have the maximum number of webhook endpoints."


class WebhookEndpointDuplicate(APIException):
    """A developer tried to register a URL they already have an endpoint for.

    409 rather than 400: the URL is valid; it conflicts with an existing
    endpoint, which the developer should edit instead.
    """

    status_code = status.HTTP_409_CONFLICT
    default_code = "webhook_endpoint_duplicate"
    default_detail = "You already have a webhook endpoint for this URL. Edit it instead."


class LastWebhookEndpoint(APIException):
    """A developer tried to delete their only endpoint.

    Webhooks are the only push channel, so an account always keeps at least
    one; the developer changes its URL or events instead.
    """

    status_code = status.HTTP_409_CONFLICT
    default_code = "last_webhook_endpoint"
    default_detail = (
        "This is your only webhook endpoint. Change its URL or events instead "
        "of deleting it."
    )

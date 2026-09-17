from rest_framework import status
from rest_framework.exceptions import APIException


class BadgeConflict(APIException):
    """Raised when a write collides with an existing live badge or award.

    409 rather than 400: the payload is well-formed, but it conflicts with
    the current state - a title already in use, a criterion/count pair
    another badge already occupies, or a creator who already holds the badge.
    """

    status_code = status.HTTP_409_CONFLICT
    default_code = "badge_conflict"
    default_detail = "This conflicts with an existing badge."

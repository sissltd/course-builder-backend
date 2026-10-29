from rest_framework import status
from rest_framework.exceptions import APIException


class CategoryDeletionNeedsStrategy(APIException):
    """Raised when deleting a category would take courses with it.

    409 rather than 400 because the request itself is well-formed - it
    conflicts with the current state of the resource, and the caller resolves
    it by making a decision (reassign or delete), not by fixing their payload.
    The distinction matters to the frontend: this is the signal to open the
    "what should happen to these courses?" dialog rather than to show a field
    error.
    """

    status_code = status.HTTP_409_CONFLICT
    default_code = "category_deletion_needs_strategy"
    default_detail = (
        "This category still has courses. Choose whether to move them to "
        "another category or delete them."
    )

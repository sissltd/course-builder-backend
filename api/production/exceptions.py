from rest_framework import status
from rest_framework.exceptions import APIException


class ProductionRunConflict(APIException):
    """The run is not in a state that allows this action (e.g. retrying a
    completed run). 409: the request is valid, the run's state forbids it."""

    status_code = status.HTTP_409_CONFLICT
    default_code = "production_run_conflict"
    default_detail = "This production run cannot do that from its current status."


class ProductionOverBudget(APIException):
    """A retry was refused because the quote still exceeds the budget."""

    status_code = status.HTTP_409_CONFLICT
    default_code = "production_over_budget"
    default_detail = (
        "The production quote is above the per-course budget. Raise "
        "`production_course_budget` in platform settings, then retry."
    )

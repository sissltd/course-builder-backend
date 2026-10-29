"""Public reference suffixes for CourseSubmission.public_reference.

The developer-facing id mutates its last segment as the submission moves;
the letters are stable API contract, so they live alone in a dependency-free
module that models and serializers can both import.
"""

from api.mie.enums import SubmissionStatus

REFERENCE_SUFFIXES = {
    SubmissionStatus.PENDING_REVIEW: "P",
    SubmissionStatus.DUPLICATE_IN_QUEUE: "D",
    SubmissionStatus.DUPLICATE_EXISTING: "E",
    SubmissionStatus.PREVIOUSLY_REJECTED: "X",
    SubmissionStatus.APPROVED: "A",
    SubmissionStatus.REJECTED: "R",
}

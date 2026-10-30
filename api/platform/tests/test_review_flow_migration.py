import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from api.courses.enums import CourseStatus
from api.courses.models import Course
from api.courses.tests.factories import make_draft_course
from api.platform.services import platform_settings_service
from api.reviews.enums import ReviewStage
from api.reviews.models import ReviewAssignment


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("enabled", [False, True])
def test_migration_preserves_existing_states_and_assignments(enabled):
    row = platform_settings_service.get_settings()
    row.staged_review_flow_enabled = enabled
    row.save(update_fields=["staged_review_flow_enabled"])
    course = make_draft_course(
        status=CourseStatus.IN_REVIEW, review_stage=ReviewStage.SECOND_REVIEW
    )
    assignment = ReviewAssignment.objects.create(
        course=course, stage=ReviewStage.SECOND_REVIEW
    )
    before = Course.objects.filter(pk=course.pk).values().get()
    before.pop("staged_review_flow")
    seat_before = ReviewAssignment.objects.filter(pk=assignment.pk).values().get()
    executor = MigrationExecutor(connection)
    try:
        executor.migrate([("courses", "0022_lesson_video_transcript")])
        # Forward to the latest state, not just 0023: the current Course
        # model is used to read the row back, and later migrations add columns.
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        after = Course.objects.filter(pk=course.pk).values().get()
        assert after.pop("staged_review_flow") is enabled
        assert after == before
        assert (
            ReviewAssignment.objects.filter(pk=assignment.pk).values().get()
            == seat_before
        )
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

"""Production runs: requested when a course starts waiting on engine-made
video, quoted against the per-course budget, and executed step by step
behind the `production_enabled` kill switch. These tests cover the run and
its storyboards; the media steps and delivery are stubbed here and tested
for real in test_lesson_video. The model is never called: a fake storyboard
provider stands in.
"""

from decimal import Decimal
from unittest import mock

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from api.courses.enums import CourseSourceType, CourseStatus, VideoProvider
from api.courses.models import Lesson, Module
from api.courses.tests.factories import build_compliant_course
from api.mie.tests.factories import make_decided_submission, make_system_developer
from api.notification.models import Notification, NotificationPreference
from api.operations.enums import CostCategory, PipelineStage
from api.operations.models import PipelineJob, ProductionCost
from api.platform.services import platform_settings_service
from api.production.enums import ProductionRunStatus
from api.production.models import ProductionRun, ProductionScene
from api.production.providers.storyboard import (
    PlannedScene,
    StoryboardRejected,
    StoryboardResult,
    check_coverage,
    split_sentences,
)
from api.production.services import production_service
from api.production.tasks import run_production
from api.reviews.enums import ReviewStage
from api.users.enums import UserRole

RUNS = "/api/v1/admin/production/runs/"
QUEUE = "/api/v1/review-queue/"


def _settings(**fields):
    row = platform_settings_service.get_settings()
    for name, value in fields.items():
        setattr(row, name, value)
    row.save()


class PairingProvider:
    """Groups the sentences two at a time. Counts calls."""

    def __init__(self):
        self.calls = 0

    def plan_lesson(self, *, course_title, lesson_title, sentences, broll_limit=0):
        self.calls += 1
        scenes = [
            PlannedScene(start, min(start + 1, len(sentences) - 1), "BULLETS", "Key point", "A slide")
            for start in range(0, len(sentences), 2)
        ]
        return StoryboardResult(scenes=scenes, input_tokens=1000, output_tokens=200)


@pytest.fixture
def provider():
    fake = PairingProvider()
    with mock.patch(
        "api.production.services.production_service.get_storyboard_provider", return_value=fake
    ):
        yield fake


@pytest.fixture(autouse=True)
def no_media():
    """Voicing, rendering and delivery are test_lesson_video's subject."""

    from api.production.services import lesson_video_service

    with (
        mock.patch("api.production.services.production_service.get_voice_providers", return_value=[]),
        mock.patch.object(lesson_video_service, "produce_lesson"),
        mock.patch.object(lesson_video_service, "produce_trailer"),
        mock.patch.object(lesson_video_service, "produce_thumbnail"),
        mock.patch.object(production_service, "_deliver", return_value="Delivered."),
    ):
        yield


@pytest.fixture
def no_dispatch():
    with mock.patch("api.production.tasks.run_production.delay") as delay:
        yield delay


@pytest.fixture
def creator(make_user):
    return make_user(role=UserRole.COURSE_CREATOR)


@pytest.fixture
def admin(make_user):
    return make_user(role=UserRole.ADMIN)


def _awaiting_video_course(creator, *, minutes_per_lesson=20, script="One. Two. Three. Four. Five."):
    course = build_compliant_course(creator=creator)
    course.status = CourseStatus.AWAITING_VIDEO
    course.save()
    Lesson.objects.filter(module__course=course).update(
        script=script, duration_minutes=minutes_per_lesson
    )
    course.duration_estimate_minutes = Lesson.objects.filter(module__course=course).count() * minutes_per_lesson
    course.save(update_fields=["duration_estimate_minutes"])
    return course


def _queued_run(course, actor):
    return production_service.request_production(course=course, actor=actor)


# --- quoting and requesting -----------------------------------------------


@pytest.mark.django_db
def test_the_quote_scales_with_the_course_length(creator):
    short = _awaiting_video_course(creator, minutes_per_lesson=5)  # 12 lessons = 60 min
    long = _awaiting_video_course(creator, minutes_per_lesson=40)  # 480 min

    assert production_service.quote_course(short) == Decimal("13.80")  # 60*0.18 + 3
    assert production_service.quote_course(long) == Decimal("89.40")  # 480*0.18 + 3


@pytest.mark.django_db
def test_a_creator_declining_the_video_hands_the_course_to_the_engine(
    api_client, creator, no_dispatch
):
    course = _awaiting_video_course(creator)
    api_client.force_authenticate(creator)

    response = api_client.post(
        f"/api/v1/courses/{course.id}/video-decision/", {"will_provide": False}, format="json"
    )

    assert response.status_code == 200
    run = ProductionRun.objects.get(course=course)
    assert (run.status, run.requested_by_id) == (ProductionRunStatus.QUEUED, creator.id)
    assert run.quote_amount == production_service.quote_course(course)
    assert run.budget_amount == Decimal("100.00")
    no_dispatch.assert_not_called()  # the engine ships switched off


@pytest.mark.django_db
def test_a_creator_who_changes_their_mind_cancels_the_run(api_client, creator, no_dispatch):
    course = _awaiting_video_course(creator)
    api_client.force_authenticate(creator)
    url = f"/api/v1/courses/{course.id}/video-decision/"
    api_client.post(url, {"will_provide": False}, format="json")

    api_client.post(url, {"will_provide": True}, format="json")
    again = api_client.post(url, {"will_provide": False}, format="json")

    assert again.status_code == 200
    statuses = sorted(ProductionRun.objects.filter(course=course).values_list("status", flat=True))
    assert statuses == sorted([ProductionRunStatus.CANCELLED, ProductionRunStatus.QUEUED])


@pytest.mark.django_db
def test_requesting_twice_returns_the_same_active_run(creator, no_dispatch):
    course = _awaiting_video_course(creator)

    first = _queued_run(course, creator)
    second = _queued_run(course, creator)

    assert first.id == second.id
    assert ProductionRun.objects.filter(course=course).count() == 1


@pytest.mark.django_db
def test_approving_a_crawler_courses_text_starts_production(api_client, make_user, no_dispatch):
    _settings(staged_review_flow_enabled=True)
    developer, _ = make_system_developer()
    idea = make_decided_submission(developer=developer, title="Crawled")
    course = build_compliant_course(creator=make_user(role=UserRole.COURSE_CREATOR))
    course.preview_video_url = ""
    course.source_type = CourseSourceType.DEVELOPER_API
    course.status = CourseStatus.SUBMITTED
    course.review_stage = ReviewStage.CONTENT
    course.save()
    idea.resulting_course = course
    idea.save(update_fields=["resulting_course"])
    writer = make_user(role=UserRole.STAFF_WRITER)
    api_client.force_authenticate(writer)

    api_client.post(f"{QUEUE}{course.id}/claim/")
    response = api_client.post(f"{QUEUE}{course.id}/approve/")

    assert response.status_code == 200
    course.refresh_from_db()
    assert course.video_provider == VideoProvider.PRODUCTION_ENGINE
    run = ProductionRun.objects.get(course=course)
    assert (run.status, run.requested_by_id) == (ProductionRunStatus.QUEUED, writer.id)


@pytest.mark.django_db
def test_a_quote_over_the_budget_blocks_the_run_and_alerts_managers(creator, make_user, no_dispatch):
    _settings(production_course_budget=Decimal("10.00"))
    admin = make_user(role=UserRole.ADMIN)
    opted_out = make_user(role=UserRole.ADMIN)
    NotificationPreference.objects.create(user=opted_out, mie_pipeline_alert=False)
    course = _awaiting_video_course(creator)

    run = _queued_run(course, creator)

    assert run.status == ProductionRunStatus.BLOCKED
    assert "above the per-course budget of $10.00" in run.status_reason
    assert Notification.objects.filter(receiver=admin, title="Production blocked by budget").exists()
    assert not Notification.objects.filter(receiver=opted_out).exists()
    no_dispatch.assert_not_called()


# --- the kill switch and dispatch -------------------------------------------


@pytest.mark.django_db
def test_switched_on_a_new_run_is_dispatched_once_committed(creator, no_dispatch):
    _settings(production_enabled=True)
    course = _awaiting_video_course(creator)

    with mock.patch("django.db.transaction.on_commit", side_effect=lambda fn: fn()):
        run = _queued_run(course, creator)

    no_dispatch.assert_called_once_with(str(run.id))
    run.refresh_from_db()
    assert run.dispatched_at is not None


@pytest.mark.django_db
def test_the_sweep_sends_a_queued_run_only_once_and_revives_silent_ones(creator, no_dispatch):
    _settings(production_enabled=True)
    queued = _queued_run(_awaiting_video_course(creator), creator)
    silent = _queued_run(_awaiting_video_course(creator), creator)
    ProductionRun.objects.filter(pk=silent.pk).update(
        status=ProductionRunStatus.RUNNING,
        heartbeat_at=timezone.now() - production_service.RUN_STALE_AFTER * 2,
        dispatched_at=timezone.now(),
    )

    first = production_service.dispatch_due_runs()
    second = production_service.dispatch_due_runs()

    assert (first, second) == (2, 0)
    assert {call.args[0] for call in no_dispatch.call_args_list} == {str(queued.id), str(silent.id)}


@pytest.mark.django_db
def test_switched_off_nothing_is_dispatched_or_claimed(creator, no_dispatch, provider):
    run = _queued_run(_awaiting_video_course(creator), creator)

    assert production_service.dispatch_due_runs() == 0
    assert production_service.execute_run(run_id=run.id) is None
    assert provider.calls == 0
    run.refresh_from_db()
    assert run.status == ProductionRunStatus.QUEUED


# --- executing: storyboards ---------------------------------------------------


@pytest.mark.django_db
def test_a_run_plans_every_lesson_verbatim_and_records_steps_and_costs(creator, no_dispatch, provider):
    _settings(production_enabled=True)
    course = _awaiting_video_course(creator)
    run = _queued_run(course, creator)
    lessons = list(Lesson.objects.filter(module__course=course))

    run_production(str(run.id))

    run.refresh_from_db()
    assert run.status == ProductionRunStatus.COMPLETED
    assert provider.calls == len(lessons)
    for lesson in lessons:
        narration = list(
            ProductionScene.objects.filter(run=run, lesson=lesson).order_by("order").values_list(
                "narration", flat=True
            )
        )
        assert " ".join(narration) == lesson.script  # the approved words, unchanged
    steps = PipelineJob.objects.filter(run=run)
    assert steps.count() == len(lessons)
    assert set(steps.values_list("stage", flat=True)) == {PipelineStage.MEDIA_PRODUCTION}
    costs = ProductionCost.objects.filter(course=course, category=CostCategory.TEXT)
    assert costs.count() == len(lessons)
    # 1,000 in at $5/M + 200 out at $30/M = $0.011 per lesson.
    assert run.spent_amount == Decimal("0.011") * len(lessons)
    course.refresh_from_db()
    assert course.status == CourseStatus.AWAITING_VIDEO  # delivery is stubbed here


@pytest.mark.django_db
def test_a_rerun_skips_planned_lessons_and_replans_only_a_changed_script(creator, no_dispatch, provider):
    _settings(production_enabled=True)
    course = _awaiting_video_course(creator)
    run = _queued_run(course, creator)
    run_production(str(run.id))
    changed = Lesson.objects.filter(module__course=course).first()
    changed.script = "Changed one. Changed two. Changed three."
    changed.save(update_fields=["script"])
    ProductionRun.objects.filter(pk=run.pk).update(status=ProductionRunStatus.QUEUED)
    calls_before = provider.calls

    run_production(str(run.id))

    assert provider.calls == calls_before + 1
    narration = " ".join(
        ProductionScene.objects.filter(run=run, lesson=changed).order_by("order").values_list(
            "narration", flat=True
        )
    )
    assert narration == changed.script


@pytest.mark.django_db
def test_switching_off_mid_run_pauses_it_back_to_the_queue(creator, no_dispatch):
    _settings(production_enabled=True)
    course = _awaiting_video_course(creator)
    run = _queued_run(course, creator)

    class SwitchOffAfterFirst(PairingProvider):
        def plan_lesson(self, **kwargs):
            _settings(production_enabled=False)
            return super().plan_lesson(**kwargs)

    fake = SwitchOffAfterFirst()
    with mock.patch(
        "api.production.services.production_service.get_storyboard_provider", return_value=fake
    ):
        run_production(str(run.id))

    run.refresh_from_db()
    assert fake.calls == 1
    assert run.status == ProductionRunStatus.QUEUED
    assert "switched off" in run.status_reason


@pytest.mark.django_db
def test_reaching_the_budget_mid_run_blocks_it(creator, no_dispatch, provider):
    _settings(production_enabled=True)
    run = _queued_run(_awaiting_video_course(creator), creator)
    ProductionRun.objects.filter(pk=run.pk).update(budget_amount=Decimal("0.02"))

    run_production(str(run.id))

    run.refresh_from_db()
    assert run.status == ProductionRunStatus.BLOCKED
    assert provider.calls == 2  # $0.011 each: the second reaches $0.02


@pytest.mark.django_db
def test_a_plan_that_keeps_skipping_sentences_retries_then_fails(creator, make_user, no_dispatch):
    _settings(production_enabled=True)
    admin = make_user(role=UserRole.ADMIN)
    run = _queued_run(_awaiting_video_course(creator), creator)
    bad = mock.Mock()
    bad.plan_lesson.side_effect = StoryboardRejected("Storyboard covers 2 of 5 sentences.")

    with mock.patch(
        "api.production.services.production_service.get_storyboard_provider", return_value=bad
    ):
        for _attempt in range(production_service.MAX_RUN_ATTEMPTS):
            run_production(str(run.id))
            run.refresh_from_db()
            if run.status == ProductionRunStatus.QUEUED:
                assert run.status_reason  # waits for the sweep, with the reason
                ProductionRun.objects.filter(pk=run.pk).update(dispatched_at=None)

    assert run.status == ProductionRunStatus.FAILED
    assert run.attempts == production_service.MAX_RUN_ATTEMPTS
    assert Notification.objects.filter(receiver=admin, title="Production failed").exists()


# --- storyboard helpers -------------------------------------------------------


def test_sentences_split_on_end_punctuation():
    assert split_sentences('First one. "Quoted two!" Third? Fourth') == [
        "First one.",
        '"Quoted two!"',
        "Third?",
        "Fourth",
    ]


@pytest.mark.parametrize(
    "ranges, ok",
    [
        ([(0, 1), (2, 4)], True),
        ([(0, 1), (3, 4)], False),  # skips sentence 2
        ([(0, 2), (2, 4)], False),  # repeats sentence 2
        ([(0, 3)], False),  # stops short
        ([(1, 4)], False),  # does not start at 0
    ],
)
def test_coverage_must_be_exact(ranges, ok):
    scenes = [PlannedScene(first, last, "BULLETS", "", "") for first, last in ranges]
    if ok:
        check_coverage(scenes, 5)
    else:
        with pytest.raises(StoryboardRejected):
            check_coverage(scenes, 5)


# --- admin endpoints ------------------------------------------------------------


@pytest.mark.django_db
def test_the_run_list_needs_pipeline_access(api_client, make_user, creator, no_dispatch):
    _queued_run(_awaiting_video_course(creator), creator)

    anonymous = api_client.get(RUNS)
    api_client.force_authenticate(make_user(role=UserRole.STAFF_APPROVER))
    refused = api_client.get(RUNS)
    api_client.force_authenticate(make_user(role=UserRole.ADMIN))
    allowed = api_client.get(RUNS)

    assert (anonymous.status_code, refused.status_code, allowed.status_code) == (401, 403, 200)
    row = allowed.data["data"]["results"][0]
    assert set(row) >= {"id", "course", "status", "quote_amount", "budget_amount", "spent_amount"}


@pytest.mark.django_db
def test_the_run_list_filters_by_status_and_course(api_client, admin, creator, no_dispatch):
    queued = _queued_run(_awaiting_video_course(creator), creator)
    other_course = _awaiting_video_course(creator)
    _settings(production_course_budget=Decimal("1.00"))
    blocked = _queued_run(other_course, creator)
    api_client.force_authenticate(admin)

    by_status = api_client.get(RUNS, {"status": "BLOCKED"})
    by_course = api_client.get(RUNS, {"course": str(queued.course_id)})
    bad_status = api_client.get(RUNS, {"status": "NOPE"})
    bad_course = api_client.get(RUNS, {"course": "not-a-uuid"})

    assert [row["id"] for row in by_status.data["data"]["results"]] == [str(blocked.id)]
    assert [row["id"] for row in by_course.data["data"]["results"]] == [str(queued.id)]
    assert (bad_status.status_code, bad_course.status_code) == (400, 400)


@pytest.mark.django_db
def test_the_run_list_costs_the_same_however_many_runs(api_client, admin, creator, no_dispatch):
    api_client.force_authenticate(admin)
    _queued_run(_awaiting_video_course(creator), creator)
    with CaptureQueriesContext(connection) as one:
        api_client.get(RUNS)
    for _ in range(4):
        _queued_run(_awaiting_video_course(creator), creator)
    with CaptureQueriesContext(connection) as five:
        api_client.get(RUNS)

    assert len(one) == len(five)


@pytest.mark.django_db
def test_the_run_detail_shows_each_lessons_progress(api_client, admin, creator, no_dispatch, provider):
    _settings(production_enabled=True)
    course = _awaiting_video_course(creator)
    run = _queued_run(course, creator)
    run_production(str(run.id))
    extra_module = Module.objects.create(course=course, title="Bonus", order=99)
    Lesson.objects.create(module=extra_module, title="Late lesson", order=1, script="Late.")
    api_client.force_authenticate(admin)

    response = api_client.get(f"{RUNS}{run.id}/")
    missing = api_client.get(f"{RUNS}5b1f0c9e-2d4a-4f7e-9a3b-8c6d1e2f3a4b/")

    assert response.status_code == 200
    lessons = response.data["lessons"]
    assert lessons[-1]["title"] == "Late lesson" and lessons[-1]["scene_count"] == 0
    assert all(row["scene_count"] == 3 for row in lessons[:-1])  # 5 sentences in pairs
    assert missing.status_code == 404


@pytest.mark.django_db
def test_the_run_detail_costs_the_same_however_many_lessons(api_client, admin, creator, no_dispatch):
    api_client.force_authenticate(admin)
    run = _queued_run(_awaiting_video_course(creator), creator)
    with CaptureQueriesContext(connection) as before:
        api_client.get(f"{RUNS}{run.id}/")
    module = Module.objects.filter(course=run.course).first()
    for order in range(100, 110):
        Lesson.objects.create(module=module, title=f"Extra {order}", order=order, script="Extra.")
    with CaptureQueriesContext(connection) as after:
        api_client.get(f"{RUNS}{run.id}/")

    assert len(before) == len(after)


@pytest.mark.django_db
def test_retry_requotes_a_blocked_run_and_refuses_while_over_budget(api_client, admin, creator, no_dispatch):
    _settings(production_course_budget=Decimal("1.00"))
    run = _queued_run(_awaiting_video_course(creator), creator)
    api_client.force_authenticate(admin)
    url = f"{RUNS}{run.id}/retry/"

    still_over = api_client.post(url)
    _settings(production_course_budget=Decimal("100.00"))
    retried = api_client.post(url)
    again = api_client.post(url)

    assert still_over.status_code == 409
    assert still_over.json()["errors"][0]["code"] == "production_over_budget"
    assert retried.status_code == 200 and retried.data["status"] == "QUEUED"
    assert retried.data["budget_amount"] == "100.00"
    assert again.status_code == 409
    assert again.json()["errors"][0]["code"] == "production_run_conflict"


@pytest.mark.django_db
def test_cancel_stops_an_active_run_once(api_client, admin, creator, no_dispatch):
    run = _queued_run(_awaiting_video_course(creator), creator)
    api_client.force_authenticate(admin)
    url = f"{RUNS}{run.id}/cancel/"

    cancelled = api_client.post(url)
    again = api_client.post(url)

    assert cancelled.status_code == 200 and cancelled.data["status"] == "CANCELLED"
    assert again.status_code == 409


@pytest.mark.django_db
@pytest.mark.parametrize("action", ["retry", "cancel"])
def test_only_production_managers_retry_or_cancel(api_client, make_user, creator, no_dispatch, action):
    run = _queued_run(_awaiting_video_course(creator), creator)
    api_client.force_authenticate(make_user(role=UserRole.STAFF_WRITER))

    response = api_client.post(f"{RUNS}{run.id}/{action}/")

    assert response.status_code == 403
    run.refresh_from_db()
    assert run.status == ProductionRunStatus.QUEUED


# --- platform settings -----------------------------------------------------------


@pytest.mark.django_db
def test_production_settings_ship_off_with_a_100_dollar_budget_and_30_minute_courses(api_client, admin):
    api_client.force_authenticate(admin)

    response = api_client.get("/api/v1/platform-settings/")
    changed = api_client.patch(
        "/api/v1/platform-settings/",
        {"production_enabled": True, "production_course_budget": "150.00"},
        format="json",
    )
    negative = api_client.patch(
        "/api/v1/platform-settings/", {"production_course_budget": "-1"}, format="json"
    )

    assert response.data["production_enabled"] is False
    assert response.data["production_course_budget"] == "100.00"
    assert response.data["course_duration_min_minutes"] == 30
    assert changed.data["production_enabled"] is True
    assert changed.data["production_course_budget"] == "150.00"
    assert negative.status_code == 400

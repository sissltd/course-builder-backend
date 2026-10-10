"""The Production Engine's run lifecycle.

A VIDEO run is requested when a course starts waiting on engine-made video,
or when a reviewer sends engine-made video back with fixes the engine can
make. It is quoted before anything is spent; a quote over the per-course
budget blocks it. A worker then plans each lesson's storyboard, voices,
draws, assembles and checks its video, makes the trailer and thumbnail, and
delivers the lot: to the second review seat for a first production, back
to the rejecting seat for a rework. A PACKAGE run is requested when a
course is published, and builds its final package and channel deliveries
(packaging_service).

Each step is recorded as a `PipelineJob` (the APE Pipeline dashboard) and
each paid call as a `ProductionCost`. Every step is idempotent: work whose
inputs did not change is reused, so a retried or re-dispatched run picks up
where it stopped and a rework pays only for what it changes.
"""

import hashlib
import logging
import re
import tempfile
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from api.authentication.services.activity_service import log_activity
from api.courses.enums import CourseStatus, VideoProvider
from api.courses.models import Course, Lesson
from api.operations.enums import CostCategory, PipelineStage, ProviderKind
from api.platform.services import platform_settings_service
from api.production.enums import (
    ACTIVE_RUN_STATUSES,
    RETRYABLE_RUN_STATUSES,
    ProductionRunStatus,
    ReworkAction,
    RunKind,
)
from api.production.exceptions import ProductionOverBudget, ProductionRunConflict
from api.production.models import ProductionAsset, ProductionRun, ProductionScene, PronunciationEntry
from api.production.providers import broll
from api.production.providers.storyboard import (
    STORYBOARD_PROMPT_VERSION,
    StoryboardRejected,
    get_storyboard_provider,
    split_sentences,
)
from api.production.providers.voice import get_voice_providers
from api.production.services import asset_store, lesson_video_service, packaging_service
from api.production.services.run_ledger import RunLedger, Stop, alert, finish, log, text_cost
from api.reviews.enums import MediaAssetKind, ReviewFlagType, ReviewStage
from api.reviews.services import quality_check_service
from api.users.enums import AccountStatus, UserActivityActionEnums, UserActivityCategoryEnums, UserRole

logger = logging.getLogger(__name__)

QUOTE_USD_PER_FINISHED_MINUTE = Decimal("0.18")
"""Planning rate for faceless narrated-scene video: narration about $0.09,
storyboard and visuals about $0.04, automated checks about $0.02 per finished
minute, plus about 20% for retries and rework."""

QUOTE_FIXED_USD_PER_COURSE = Decimal("3.00")
"""The course trailer and thumbnail, whatever the course length."""

MONEY = Decimal("0.01")
COST_PRECISION = Decimal("0.0001")

STORYBOARD_ATTEMPTS = 2
"""A plan that does not cover the script exactly is asked for once more."""

MAX_RUN_ATTEMPTS = 3
"""Worker starts before a run that keeps failing transiently is marked failed."""

RUN_STALE_AFTER = timedelta(minutes=45)
"""A running run without a heartbeat for this long has lost its worker. The
worker beats between steps; the longest single step is rendering one
lesson, which takes minutes, so this leaves a wide margin."""

REDISPATCH_AFTER = timedelta(minutes=10)
"""A queued run handed to a worker is not handed again for this long."""

TEXT_PROVIDER_NAMES = {"openai": "OpenAI"}
"""Display names in the provider registry for COURSE_AI_PROVIDER values."""

ENGINE_EMAIL = "production-engine@soludesks.invalid"


# --- quoting -------------------------------------------------------------


def course_minutes(course: Course) -> int:
    """The course's runtime, the figure every production cost scales with."""

    return course.duration_estimate_minutes or quality_check_service.get_course_duration_minutes(
        course
    )


def quote_run(course: Course, kind: str) -> Decimal:
    """What a run of `kind` is expected to cost. Packaging makes no paid
    calls."""

    return Decimal("0.00") if kind == RunKind.PACKAGE else quote_course(course)


def quote_course(course: Course) -> Decimal:
    """Estimated cost of producing `course` from its own length, in USD,
    plus its b-roll allowance when b-roll is on."""

    quote = Decimal(course_minutes(course)) * QUOTE_USD_PER_FINISHED_MINUTE + QUOTE_FIXED_USD_PER_COURSE
    clips = platform_settings_service.get_settings().production_broll_per_lesson
    if clips and broll.is_configured():
        lessons = Lesson.objects.filter(module__course=course).count()
        quote += lessons * clips * broll.CLIP_SECONDS * Decimal(settings.PRODUCTION_BROLL_USD_PER_SECOND)
    return quote.quantize(MONEY, rounding=ROUND_HALF_UP)


def _over_budget_reason(*, quote: Decimal, budget: Decimal) -> str:
    return (
        f"The quote of ${quote} is above the per-course budget of ${budget}. "
        "Raise the budget, then retry."
    )


# --- requesting, cancelling, retrying -------------------------------------


def request_production(
    *, course: Course, actor, kind: str = RunKind.VIDEO, instructions: dict | None = None
) -> ProductionRun:
    """Hand `course` to the engine: quote it and queue it, or block it.

    Called when a course starts waiting on engine-made video (kind VIDEO),
    when a reviewer sends engine-made video back with fixes the engine can
    make (VIDEO with rework `instructions`), and when a course is published
    (PACKAGE). Idempotent: a course already holding an active run gets that
    run back, with any new rework instructions merged in. Runs inside the
    caller's transaction; the worker is woken only once it commits.
    """

    existing = (
        ProductionRun.objects.select_for_update()
        .filter(course=course, status__in=ACTIVE_RUN_STATUSES)
        .first()
    )
    if existing is not None:
        if instructions:
            existing.instructions = merge_instructions(existing.instructions, instructions)
            existing.save(update_fields=["instructions", "updated_datetime"])
        return existing

    budget = platform_settings_service.get_settings().production_course_budget
    quote = quote_run(course, kind)
    over_budget = quote > budget
    run = ProductionRun.objects.create(
        course=course,
        kind=kind,
        instructions=instructions or {},
        requested_by=actor,
        quote_amount=quote,
        budget_amount=budget,
        status=ProductionRunStatus.BLOCKED if over_budget else ProductionRunStatus.QUEUED,
        status_reason=_over_budget_reason(quote=quote, budget=budget) if over_budget else "",
    )
    what = "Final packaging" if kind == RunKind.PACKAGE else ("Video rework" if instructions else "Video production")
    log(run, UserActivityActionEnums.PRODUCTION_REQUESTED, f"{what} for '{course.title}' was requested, quoted at ${quote}.")
    if over_budget:
        alert(run, title="Production blocked by budget", content=run.status_reason)
    else:
        transaction.on_commit(lambda: dispatch_run(run_id=run.id))
    return run


def request_packaging(*, course_id, actor) -> ProductionRun:
    """Admin: build and deliver a published course's final package again
    (after a channel was configured or a mapping changed). Channels already
    live are left alone. 404 for an unknown course; 409 unless published."""

    with transaction.atomic():
        course = get_course(course_id=course_id)
        if course.status != CourseStatus.PUBLISHED:
            raise ProductionRunConflict("Only a published course can be packaged.")
        return request_production(course=course, actor=actor, kind=RunKind.PACKAGE)


def get_course(*, course_id) -> Course:
    course = Course.objects.filter(pk=course_id).first()
    if course is None:
        from rest_framework.exceptions import NotFound

        raise NotFound("Course not found.")
    return course


def merge_instructions(current: dict, extra: dict) -> dict:
    lessons = {key: list(value) for key, value in (current.get("lessons") or {}).items()}
    for lesson_id, actions in (extra.get("lessons") or {}).items():
        lessons[lesson_id] = sorted(set(lessons.get(lesson_id, [])) | set(actions))
    flags = list(dict.fromkeys([*(current.get("flags") or []), *(extra.get("flags") or [])]))
    return {"lessons": lessons, "flags": flags}


def cancel_active_run(*, course: Course, actor, reason: str) -> ProductionRun | None:
    """Cancel the course's active run, if it has one (e.g. the creator chose
    to make the video after all). Returns the cancelled run or None."""

    run = (
        ProductionRun.objects.select_for_update()
        .filter(course=course, status__in=ACTIVE_RUN_STATUSES)
        .first()
    )
    if run is None:
        return None
    return _cancel(run=run, actor=actor, reason=reason)


def cancel_run(*, run: ProductionRun, actor) -> ProductionRun:
    """Admin cancel. 409 for a run that has already finished."""

    with transaction.atomic():
        run = ProductionRun.objects.select_for_update().get(pk=run.pk)
        if run.status not in ACTIVE_RUN_STATUSES:
            raise ProductionRunConflict(f"A {run.get_status_display().lower()} run cannot be cancelled.")
        return _cancel(run=run, actor=actor, reason="Cancelled by an admin.")


def _cancel(*, run: ProductionRun, actor, reason: str) -> ProductionRun:
    run.status = ProductionRunStatus.CANCELLED
    run.status_reason = reason
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "status_reason", "finished_at", "updated_datetime"])
    log_activity(
        user=actor,
        category=UserActivityCategoryEnums.PRODUCTION,
        action=UserActivityActionEnums.PRODUCTION_CANCELLED,
        summary=f"Video production for '{run.course.title}' was cancelled: {reason}",
        target=run.course,
    )
    return run


def retry_run(*, run: ProductionRun, actor) -> ProductionRun:
    """Admin retry of a failed or budget-blocked run: re-quoted against the
    budget in force now, then queued. 409 if it still does not fit, or if the
    run is not failed or blocked."""

    with transaction.atomic():
        run = ProductionRun.objects.select_for_update().get(pk=run.pk)
        if run.status not in RETRYABLE_RUN_STATUSES:
            raise ProductionRunConflict(
                f"Only failed or blocked runs can be retried; this one is {run.get_status_display().lower()}."
            )
        budget = platform_settings_service.get_settings().production_course_budget
        quote = quote_run(run.course, run.kind)
        if quote > budget:
            raise ProductionOverBudget(_over_budget_reason(quote=quote, budget=budget))
        run.quote_amount = quote
        run.budget_amount = budget
        run.status = ProductionRunStatus.QUEUED
        run.status_reason = ""
        run.attempts = 0
        run.dispatched_at = None
        run.finished_at = None
        run.save(
            update_fields=[
                "quote_amount",
                "budget_amount",
                "status",
                "status_reason",
                "attempts",
                "dispatched_at",
                "finished_at",
                "updated_datetime",
            ]
        )
        log_activity(
            user=actor,
            category=UserActivityCategoryEnums.PRODUCTION,
            action=UserActivityActionEnums.PRODUCTION_RETRIED,
            summary=f"Video production for '{run.course.title}' was retried.",
            target=run.course,
        )
        transaction.on_commit(lambda: dispatch_run(run_id=run.id))
    return run


# --- dispatching -------------------------------------------------------------


def dispatch_run(*, run_id) -> bool:
    """Hand a queued run to a worker, unless the engine is switched off or it
    was handed out recently. Returns whether it was sent."""

    if not platform_settings_service.get_settings().production_enabled:
        return False
    now = timezone.now()
    claimed = (
        ProductionRun.objects.filter(id=run_id, status=ProductionRunStatus.QUEUED)
        .exclude(dispatched_at__gt=now - REDISPATCH_AFTER)
        .update(dispatched_at=now)
    )
    if not claimed:
        return False
    from api.production.tasks import run_production

    run_production.delay(str(run_id))
    return True


def dispatch_due_runs() -> int:
    """The beat sweep: requeue runs whose worker went silent, then hand every
    due queued run to a worker. Returns how many were sent. Does nothing while
    the engine is switched off."""

    if not platform_settings_service.get_settings().production_enabled:
        return 0
    now = timezone.now()
    ProductionRun.objects.filter(
        status=ProductionRunStatus.RUNNING, heartbeat_at__lt=now - RUN_STALE_AFTER
    ).update(status=ProductionRunStatus.QUEUED, dispatched_at=None, updated_datetime=now)
    due = ProductionRun.objects.filter(status=ProductionRunStatus.QUEUED).values_list("id", flat=True)
    return sum(dispatch_run(run_id=run_id) for run_id in list(due))


# --- executing ---------------------------------------------------------------


def execute_run(*, run_id) -> ProductionRun | None:
    """Run every step of the run, skipping work already done. Called by the
    worker task; returns None when the run was not claimable."""

    run = _claim(run_id)
    if run is None:
        return None
    ledger = RunLedger(run)
    try:
        if run.kind == RunKind.PACKAGE:
            packaging_service.package_course(ledger=ledger)
            summary = f"The final package of '{run.course.title}' is built and delivered."
        else:
            summary = _produce_video(ledger)
    except Stop:
        return run
    finally:
        ledger.tracer.flush()
    finish(run, ProductionRunStatus.COMPLETED, reason="")
    log(run, UserActivityActionEnums.PRODUCTION_COMPLETED, f"{summary} (spent ${run.spent_amount.quantize(MONEY)})")
    return run


def fail_run(*, run_id, error: str, will_retry: bool) -> None:
    """Record a failed attempt: back to the queue while attempts remain,
    otherwise failed, with an alert to whoever manages production."""

    with transaction.atomic():
        run = (
            ProductionRun.objects.select_for_update(of=("self",))
            .select_related("course", "requested_by")
            .get(pk=run_id)
        )
        if run.status != ProductionRunStatus.RUNNING:
            return
        if will_retry and run.attempts < MAX_RUN_ATTEMPTS:
            run.status = ProductionRunStatus.QUEUED
            run.status_reason = error[:500]
            run.dispatched_at = None
            run.save(update_fields=["status", "status_reason", "dispatched_at", "updated_datetime"])
            return
        finish(run, ProductionRunStatus.FAILED, reason=error)
        log(run, UserActivityActionEnums.PRODUCTION_FAILED, f"Video production for '{run.course.title}' failed: {error}")
        alert(run, title="Production failed", content=run.status_reason)


def _claim(run_id) -> ProductionRun | None:
    with transaction.atomic():
        # Lock only the run row: Postgres refuses FOR UPDATE across the
        # nullable requested_by join.
        run = (
            ProductionRun.objects.select_for_update(of=("self",))
            .select_related("course", "requested_by")
            .filter(pk=run_id)
            .first()
        )
        if run is None or run.status != ProductionRunStatus.QUEUED:
            return None
        if not platform_settings_service.get_settings().production_enabled:
            return None
        now = timezone.now()
        run.status = ProductionRunStatus.RUNNING
        run.attempts += 1
        run.started_at = run.started_at or now
        run.heartbeat_at = now
        run.save(update_fields=["status", "attempts", "started_at", "heartbeat_at", "updated_datetime"])
        return run


def _lessons_in_order(course: Course):
    return Lesson.objects.filter(module__course=course).order_by("module__order", "order")


def _script_hash(script: str) -> str:
    return hashlib.sha256(script.encode()).hexdigest()


def _produce_video(ledger: RunLedger) -> str:
    """Storyboard, voice, draw, assemble and check every lesson, make the
    trailer and thumbnail, then deliver. Returns the activity summary."""

    run = ledger.run
    course = run.course
    lessons = list(_lessons_in_order(course))
    actions = (run.instructions or {}).get("lessons") or {}
    text_provider = ledger.provider(TEXT_PROVIDER_NAMES.get(settings.COURSE_AI_PROVIDER, settings.COURSE_AI_PROVIDER), ProviderKind.TEXT)
    storyboards = {
        lesson.id: _plan_lesson(
            ledger=ledger,
            lesson=lesson,
            provider=text_provider,
            replan=ReworkAction.RESTORYBOARD in actions.get(str(lesson.id), []),
        )
        for lesson in lessons
    }

    voices = get_voice_providers()
    lexicon = dict(PronunciationEntry.objects.filter(course=course).values_list("term", "spoken_as"))
    videos = {}
    for lesson in lessons:
        with tempfile.TemporaryDirectory(prefix="pe-lesson-") as workdir:
            videos[lesson.id] = lesson_video_service.produce_lesson(
                ledger=ledger,
                lesson=lesson,
                scenes=storyboards[lesson.id],
                actions=actions.get(str(lesson.id), []),
                voices=voices,
                lexicon=lexicon,
                workdir=Path(workdir),
            )
    with tempfile.TemporaryDirectory(prefix="pe-course-") as workdir:
        trailer = lesson_video_service.produce_trailer(ledger=ledger, voices=voices, lexicon=lexicon, workdir=Path(workdir))
        thumbnail = lesson_video_service.produce_thumbnail(ledger=ledger, workdir=Path(workdir))
    ledger.checkpoint()
    return _deliver(ledger=ledger, lessons=lessons, storyboards=storyboards, videos=videos, trailer=trailer, thumbnail=thumbnail)


def _plan_lesson(*, ledger: RunLedger, lesson: Lesson, provider, replan: bool) -> list[ProductionScene]:
    """The lesson's storyboard for this run. Reused, in this order: planned
    by this run already; planned by an earlier run from the same script
    (copied, unless a reviewer asked for the scenes to be redone); else
    planned now."""

    run = ledger.run
    script_hash = _script_hash(lesson.script or "")
    own = list(ProductionScene.objects.filter(run=run, lesson=lesson, script_hash=script_hash).order_by("order"))
    if own:
        return own
    if not replan:
        earlier = (
            ProductionScene.objects.filter(lesson=lesson, script_hash=script_hash)
            .exclude(run=run)
            .order_by("-run__created_datetime")
            .values_list("run_id", flat=True)
            .first()
        )
        if earlier is not None:
            copies = [
                ProductionScene(
                    run=run,
                    lesson=lesson,
                    order=scene.order,
                    scene_type=scene.scene_type,
                    narration=scene.narration,
                    on_screen_text=scene.on_screen_text,
                    visual_brief=scene.visual_brief,
                    bullets=scene.bullets,
                    code=scene.code,
                    script_hash=script_hash,
                )
                for scene in ProductionScene.objects.filter(run_id=earlier, lesson=lesson).order_by("order")
            ]
            with transaction.atomic():
                ProductionScene.objects.filter(run=run, lesson=lesson).delete()
                return ProductionScene.objects.bulk_create(copies)
    ledger.checkpoint()

    sentences = split_sentences(lesson.script or "")
    if not sentences:
        raise ValueError(f"Lesson '{lesson.title}' has no script to narrate.")

    started_at = timezone.now()
    broll_limit = platform_settings_service.get_settings().production_broll_per_lesson if broll.is_configured() else 0
    result = None
    for attempt in range(STORYBOARD_ATTEMPTS):
        call_started = timezone.now()
        try:
            result = get_storyboard_provider().plan_lesson(
                course_title=run.course.title, lesson_title=lesson.title, sentences=sentences, broll_limit=broll_limit
            )
        except StoryboardRejected as exc:
            ledger.tracer.generation(
                name="storyboard", model=settings.OPENAI_TEXT_MODEL, started_at=call_started,
                input={"lesson": lesson.title, "sentences": sentences}, error=str(exc.detail),
                metadata={"prompt_version": STORYBOARD_PROMPT_VERSION, "attempt": attempt + 1},
            )
            if attempt + 1 == STORYBOARD_ATTEMPTS:
                ledger.tracer.score(name="storyboard_first_try", value=0, comment=lesson.title)
                raise
            continue
        ledger.tracer.generation(
            name="storyboard", model=settings.OPENAI_TEXT_MODEL, started_at=call_started,
            input={"lesson": lesson.title, "sentences": sentences},
            output=[scene.__dict__ for scene in result.scenes],
            usage={"input": result.input_tokens, "output": result.output_tokens, "unit": "TOKENS"},
            cost=text_cost(result.input_tokens, result.output_tokens),
            metadata={"prompt_version": STORYBOARD_PROMPT_VERSION, "attempt": attempt + 1},
        )
        ledger.tracer.score(name="storyboard_first_try", value=1 if attempt == 0 else 0, comment=lesson.title)
        break

    with transaction.atomic():
        ProductionScene.objects.filter(run=run, lesson=lesson).delete()
        scenes = ProductionScene.objects.bulk_create(
            [
                ProductionScene(
                    run=run,
                    lesson=lesson,
                    order=order,
                    scene_type=scene.scene_type,
                    narration=" ".join(sentences[scene.first_sentence : scene.last_sentence + 1]),
                    on_screen_text=scene.on_screen_text,
                    visual_brief=scene.visual_brief,
                    bullets=list(scene.bullets),
                    code=scene.code,
                    script_hash=script_hash,
                )
                for order, scene in enumerate(result.scenes, start=1)
            ]
        )
        ledger.step(
            stage=PipelineStage.MEDIA_PRODUCTION, step="storyboard", started_at=started_at, lesson=lesson, provider=provider
        )
        ledger.charge(
            category=CostCategory.TEXT,
            amount=text_cost(result.input_tokens, result.output_tokens),
            provider=provider,
            note=f"Storyboard: {lesson.title}",
        )
    ledger.checkpoint()
    return scenes


# --- delivering -------------------------------------------------------------------


def engine_user():
    """The Production Engine's own account: the actor on what it delivers
    and resubmits. It can never sign in (inactive, no usable password, an
    address on a reserved domain) and holds no permissions; it only appears
    as the actor in the audit trail."""

    from api.users.models import User

    user = User.objects.filter(email=ENGINE_EMAIL).first()
    if user is not None:
        return user
    return User.objects.create_user(
        email=ENGINE_EMAIL,
        password=None,
        first_name="Production",
        last_name="Engine",
        role=UserRole.COURSE_CREATOR,
        status=AccountStatus.ACTIVE,
        is_active=False,
        terms_accepted_at=timezone.now(),
    )


def _deliver(*, ledger: RunLedger, lessons, storyboards, videos, trailer, thumbnail) -> str:
    """Attach the made video to the course and send it on: to the second
    review seat after a first production, back to the seat that asked for
    the fixes after a rework. A script edited while the run worked sends the
    run round again rather than delivering stale video."""

    from api.courses.services import course_service
    from api.reviews.models import MediaAsset

    run = ledger.run
    with transaction.atomic():
        course = Course.objects.select_for_update().get(pk=run.course_id)
        current = {lesson.id: _script_hash(lesson.script or "") for lesson in _lessons_in_order(course)}
        planned = {lesson.id: storyboards[lesson.id][0].script_hash for lesson in lessons}
        if current != planned:
            raise ScriptChangedDuringRun("A lesson script changed while the video was being made.")

        changed = []
        for lesson in lessons:
            video = videos[lesson.id]
            lesson.video_url = asset_store.public_url(video.file_key)
            lesson.video_script_file = video.data["srt_key"]
            changed.append(lesson)
        Lesson.objects.bulk_update(changed, ["video_url", "video_script_file"])
        course.preview_video_url = asset_store.public_url(trailer.file_key)
        if not course.thumbnail_url:
            course.thumbnail_url = asset_store.public_url(thumbnail.file_key)
        course.save(update_fields=["preview_video_url", "thumbnail_url", "updated_datetime"])

        MediaAsset.objects.filter(
            course=course,
            kind__in=(MediaAssetKind.VIDEO, MediaAssetKind.PREVIEW_VIDEO, MediaAssetKind.THUMBNAIL),
        ).delete()
        MediaAsset.objects.bulk_create(
            [_media_asset(course, lesson, videos[lesson.id], MediaAssetKind.VIDEO) for lesson in lessons]
            + [
                _media_asset(course, None, trailer, MediaAssetKind.PREVIEW_VIDEO),
                MediaAsset(
                    course=course,
                    kind=MediaAssetKind.THUMBNAIL,
                    url=asset_store.public_url(thumbnail.file_key),
                    mime_type=thumbnail.content_type,
                    resolution="1280x720",
                ),
            ]
        )

        actor = engine_user()
        if course.status == CourseStatus.AWAITING_VIDEO:
            course_service.deliver_video(course=course, actor=actor)
            return f"The video for '{course.title}' is made and sent to review."
        if course.status == CourseStatus.NEEDS_REVISION:
            course_service.resubmit_produced_video(course=course, actor=actor)
            return f"The video for '{course.title}' is reworked and back in review."
    return f"The video for '{course.title}' is made; the course had moved on, so it was not sent."


class ScriptChangedDuringRun(Exception):
    """Retryable: the run goes round again and remakes the changed lessons."""

    retryable = True


def _media_asset(course, lesson, video: ProductionAsset, kind: str):
    from api.reviews.models import MediaAsset

    measured = video.data["measurements"]
    return MediaAsset(
        course=course,
        lesson=lesson,
        kind=kind,
        url=asset_store.public_url(video.file_key),
        mime_type=video.content_type,
        duration_seconds=round(measured["duration_seconds"]),
        resolution=f"{measured['width']}x{measured['height']}",
        subtitle_url=asset_store.public_url(video.data["vtt_key"]),
        caption_accuracy_percent=Decimal(video.data["caption_accuracy_percent"]),
        audio_lufs=Decimal(str(measured["integrated_lufs"])),
        audio_video_drift_ms=measured["drift_ms"],
        accessibility={
            "captions": ["vtt", "srt"],
            "caption_srt_url": asset_store.public_url(video.data["srt_key"]),
            "transcript_url": asset_store.public_url(video.data["transcript_key"]),
            "language": "en",
            "narration_describes_visuals": True,
            "ai_narration_disclosed": True,
        },
        verification={"source": "production_engine", "voice": video.data.get("voice", ""), **measured},
    )


# --- rework -------------------------------------------------------------------------


#: Which flag types the engine fixes itself, and how. The content types are
#: missing on purpose: they change approved words, which only the course's
#: author may do (they edit and resubmit, and the engine remakes the lessons
#: whose script changed).
FLAG_ACTIONS = {
    ReviewFlagType.PRONUNCIATION: ReworkAction.REVOICE,
    ReviewFlagType.VOICE_QUALITY: ReworkAction.REVOICE,
    ReviewFlagType.PACING: ReworkAction.REVOICE,
    ReviewFlagType.AUDIO_LEVEL: ReworkAction.RERENDER,
    ReviewFlagType.CAPTIONS: ReworkAction.RERENDER,
    ReviewFlagType.VISUAL_ERROR: ReworkAction.RESTORYBOARD,
    ReviewFlagType.ON_SCREEN_TEXT: ReworkAction.RESTORYBOARD,
    ReviewFlagType.VISUAL_QUALITY: ReworkAction.RESTORYBOARD,
}

#: Seats that review the video; a rejection at the first seat is about text.
VIDEO_SEATS = (ReviewStage.SECOND_REVIEW, ReviewStage.VERIFICATION, ReviewStage.QA)

PRONUNCIATION_LINE = re.compile(r"^\s*(?P<term>[^=\n]{1,100}?)\s*=\s*(?P<spoken>[^\n]{1,200}?)\s*$", re.M)


def handle_rejection(*, course: Course, review_action, reviewer) -> ProductionRun | None:
    """After a reviewer sends back a course whose video the engine made:
    when every flag is one the engine can fix, start a rework run that
    redoes just those lessons and resubmits to the same seat. Otherwise the
    course waits for its author, as any rejected course does. Returns the
    rework run, or None.

    PRONUNCIATION flags may carry `term = spoken form` lines in their note;
    each becomes a pronunciation for the course.
    """

    if course.video_provider != VideoProvider.PRODUCTION_ENGINE or not course.video_attached_at:
        return None
    if review_action.stage not in VIDEO_SEATS:
        return None
    flags = list(review_action.flags.select_related("module"))
    if not flags:
        return None
    lessons_by_module: dict = {}
    for lesson_id, module_id in Lesson.objects.filter(module__course=course).values_list("id", "module_id"):
        lessons_by_module.setdefault(module_id, []).append(str(lesson_id))
    every_lesson = [lesson_id for ids in lessons_by_module.values() for lesson_id in ids]

    lessons: dict[str, set] = {}
    for flag in flags:
        action = FLAG_ACTIONS.get(flag.flag_type.upper())
        if action is None:
            return None
        if flag.lesson_id:
            targets = [str(flag.lesson_id)]
        elif flag.module_id:
            targets = lessons_by_module.get(flag.module_id, [])
        else:
            targets = every_lesson
        for lesson_id in targets:
            lessons.setdefault(lesson_id, set()).add(action)
        if flag.flag_type.upper() == ReviewFlagType.PRONUNCIATION:
            for match in PRONUNCIATION_LINE.finditer(flag.reviewer_note or ""):
                PronunciationEntry.objects.update_or_create(
                    course=course, term=match["term"], defaults={"spoken_as": match["spoken"]}
                )
    instructions = {
        "lessons": {lesson_id: sorted(actions) for lesson_id, actions in lessons.items()},
        "flags": [str(flag.id) for flag in flags],
    }
    return request_production(course=course, actor=reviewer, instructions=instructions)


def remake_after_resubmission(*, course: Course, actor) -> ProductionRun | None:
    """The author resubmitted an engine-video course after fixing its text:
    the engine remakes the lessons whose script changed (unchanged lessons
    are reused) and resubmits to the rejecting seat. Returns the run, or
    None when the course's video is not the engine's to remake."""

    if course.video_provider != VideoProvider.PRODUCTION_ENGINE or not course.video_attached_at:
        return None
    if course.revision_seat not in VIDEO_SEATS:
        return None
    return request_production(course=course, actor=actor)


# --- reading -------------------------------------------------------------------


def runs_queryset(*, status: str | None = None, course_id=None):
    """Runs for the admin list, newest first, with what the rows display."""

    queryset = ProductionRun.objects.select_related("course", "requested_by").order_by(
        "-created_datetime"
    )
    if status:
        queryset = queryset.filter(status=status)
    if course_id:
        queryset = queryset.filter(course_id=course_id)
    return queryset


def get_run(*, run_id) -> ProductionRun:
    run = runs_queryset().filter(pk=run_id).first()
    if run is None:
        from rest_framework.exceptions import NotFound

        raise NotFound("Production run not found.")
    return run


def lesson_progress(run: ProductionRun) -> list[dict]:
    """Every lesson of the run's course, in order, with how far this run got
    with it. One query, whatever the course size."""

    from django.db.models import Count, Q

    from api.operations.enums import PipelineJobStatus

    return [
        {
            "lesson_id": lesson.id,
            "title": lesson.title,
            "scene_count": lesson.scene_count,
            "steps_completed": lesson.steps_completed,
            "steps_failed": lesson.steps_failed,
        }
        for lesson in _lessons_in_order(run.course).annotate(
            scene_count=Count("production_scenes", filter=Q(production_scenes__run=run), distinct=True),
            steps_completed=Count(
                "pipeline_jobs",
                filter=Q(pipeline_jobs__run=run, pipeline_jobs__status=PipelineJobStatus.COMPLETED),
                distinct=True,
            ),
            steps_failed=Count(
                "pipeline_jobs",
                filter=Q(pipeline_jobs__run=run, pipeline_jobs__status=PipelineJobStatus.FAILED),
                distinct=True,
            ),
        )
    ]

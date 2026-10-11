"""The engine's extras: the frame-by-frame visual check, faceless AI b-roll
from Veo, and evaluation tracing to Langfuse. Vendors are faked; ffmpeg runs
for real."""

import subprocess
from decimal import Decimal
from unittest import mock

import httpx
import pytest
from django.test import override_settings

from api.courses.ai.providers import AIProviderError
from api.operations.enums import CostCategory
from api.operations.models import ProductionCost
from api.production.enums import AssetKind, ProductionRunStatus, SceneType
from api.production.models import ProductionAsset
from api.production.providers import broll, visual_check
from api.production.providers.storyboard import PlannedScene, limit_broll, storyboard_schema
from api.production.services import production_service
from api.production.tests.conftest import set_settings
from api.production.tests.test_lesson_video import _awaiting_engine_video, _produce
from api.reviews.enums import MediaAssetKind
from api.reviews.models import MediaAsset
from api.users.enums import UserRole

LANGFUSE = {"LANGFUSE_PUBLIC_KEY": "pk-test", "LANGFUSE_SECRET_KEY": "sk-test", "LANGFUSE_HOST": "https://langfuse.test"}


@pytest.fixture
def engine(db):
    set_settings(production_enabled=True, staged_review_flow_enabled=True, course_module_count_min=1, course_lessons_per_module_min=1)


@pytest.fixture
def creator(make_user):
    return make_user(role=UserRole.COURSE_CREATOR)


@pytest.fixture
def media(engine, store, voices, hearing, storyboard, eyes, no_dispatch):
    return {"store": store, "primary": voices[0], "storyboard": storyboard, "eyes": eyes}


@pytest.fixture(scope="session")
def clip_bytes(tmp_path_factory) -> bytes:
    path = tmp_path_factory.mktemp("clip") / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=24:duration=2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )
    return path.read_bytes()


# --- visual check ------------------------------------------------------------------------


@pytest.mark.django_db
def test_every_scene_of_every_lesson_is_looked_at_and_its_cost_recorded(creator, media):
    course = _awaiting_engine_video(creator)

    run = _produce(course, creator)

    assert run.status == ProductionRunStatus.COMPLETED, run.status_reason
    assert media["eyes"].frames_seen == 2  # one scene per lesson, two lessons
    assert ProductionCost.objects.filter(course=course, note__startswith="Visual check:").count() == 2


@pytest.mark.django_db
def test_a_visual_problem_retakes_the_lesson_then_fails_the_run_naming_the_scene(creator, media):
    course = _awaiting_engine_video(creator)
    media["eyes"].problems = ["The heading is cut off at the right edge."]

    run = _produce(course, creator)

    assert run.status == ProductionRunStatus.FAILED
    assert "visual check, scene 1 (BULLETS): The heading is cut off" in run.status_reason
    assert media["eyes"].frames_seen == 2  # the first lesson, twice
    assert not MediaAsset.objects.filter(course=course).exists()


@pytest.mark.django_db
def test_switched_off_the_visual_check_is_skipped(creator, media):
    set_settings(production_visual_check_enabled=False)
    course = _awaiting_engine_video(creator)
    media["eyes"].problems = ["Would fail if asked."]

    run = _produce(course, creator)

    assert run.status == ProductionRunStatus.COMPLETED, run.status_reason
    assert media["eyes"].frames_seen == 0


def test_the_visual_check_looks_at_frames_in_batches_and_maps_problems_back():
    calls = []

    def respond(*, name, schema, prompt, images):
        calls.append(images)
        first = 0 if len(calls) == 1 else visual_check.FRAMES_PER_CALL
        verdicts = [{"index": first + offset, "ok": True, "problems": []} for offset in range(len(images))]
        if len(calls) == 2:
            verdicts[1] = {"index": first + 1, "ok": False, "problems": ["A face is visible."]}
        return {"frames": verdicts}, {"input_tokens": 100, "output_tokens": 10}

    provider = mock.Mock(structured_response=respond)
    frames = [visual_check.Frame(jpeg=b"\xff\xd8jpeg", scene_type="IMAGE", on_screen_text="T", bullets=[]) for _ in range(10)]
    with mock.patch.object(visual_check, "get_course_ai_provider", return_value=provider):
        verdict = visual_check.check(frames)

    assert [len(images) for images in calls] == [8, 2]
    assert calls[0][0].startswith("data:image/jpeg;base64,")
    assert verdict.problems == {9: ["A face is visible."]}
    assert (verdict.input_tokens, verdict.output_tokens) == (200, 20)


# --- b-roll ------------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(GEMINI_API_KEY="gemini-test")
def test_a_broll_scene_is_filmed_mixed_with_stills_and_paid_for(creator, media, clip_bytes):
    set_settings(production_broll_per_lesson=1)
    course = _awaiting_engine_video(creator)
    quote_with_broll = production_service.quote_course(course)
    media["storyboard"].scene_type = SceneType.BROLL

    with mock.patch.object(broll, "generate", return_value=broll.Clip(video=clip_bytes, seconds=8, cost=Decimal("1.20"))) as generate:
        run = _produce(course, creator)

    assert run.status == ProductionRunStatus.COMPLETED, run.status_reason
    assert media["storyboard"].broll_limits == [1, 1]
    assert generate.call_count == 2
    assert ProductionAsset.objects.filter(course=course, kind=AssetKind.BROLL).count() == 2
    assert ProductionCost.objects.filter(course=course, category=CostCategory.VIDEO, amount=Decimal("1.20")).count() == 2
    evidence = MediaAsset.objects.filter(course=course, kind=MediaAssetKind.VIDEO).first()
    assert evidence.resolution == "1920x1080" and evidence.audio_video_drift_ms <= 100
    # Two lessons x one clip x 8 s x $0.15 on top of the length-based quote.
    with override_settings(GEMINI_API_KEY=""):
        assert quote_with_broll - production_service.quote_course(course) == Decimal("2.40")


@pytest.mark.django_db
@override_settings(GEMINI_API_KEY="gemini-test")
def test_when_veo_cannot_make_a_clip_the_scene_is_drawn_as_a_still(creator, media):
    set_settings(production_broll_per_lesson=1)
    course = _awaiting_engine_video(creator)
    media["storyboard"].scene_type = SceneType.BROLL

    with (
        mock.patch.object(broll, "generate", side_effect=AIProviderError(detail="filtered", retryable=False)),
        mock.patch("api.production.providers.media_ai.illustrate", side_effect=AIProviderError(detail="no", retryable=False)),
    ):
        run = _produce(course, creator)

    assert run.status == ProductionRunStatus.COMPLETED, run.status_reason
    assert not ProductionAsset.objects.filter(course=course, kind=AssetKind.BROLL).exists()


@pytest.mark.django_db
def test_without_a_gemini_key_no_broll_is_offered(creator, media):
    set_settings(production_broll_per_lesson=3)
    course = _awaiting_engine_video(creator)

    _produce(course, creator)

    assert media["storyboard"].broll_limits == [0, 0]


def test_broll_past_the_allowance_becomes_illustrations_and_off_is_not_offered():
    scenes = [PlannedScene(index, index, SceneType.BROLL, "", "Waves") for index in range(3)]

    assert [scene.scene_type for scene in limit_broll(scenes, 1)] == [SceneType.BROLL, SceneType.IMAGE, SceneType.IMAGE]
    enum = storyboard_schema(allow_broll=False)["properties"]["scenes"]["items"]["properties"]["scene_type"]["enum"]
    assert SceneType.BROLL not in enum


@override_settings(GEMINI_API_KEY="gemini-test", VEO_MODEL="veo-test", PRODUCTION_BROLL_USD_PER_SECOND="0.15")
def test_veo_is_asked_for_a_faceless_clip_polled_and_downloaded():
    def respond(method, url, **kwargs):
        request = httpx.Request(method, url)
        if url.endswith(":predictLongRunning"):
            assert kwargs["json"]["parameters"]["personGeneration"] == "dont_allow"
            return httpx.Response(200, json={"name": "operations/1", "done": False}, request=request)
        if url.endswith("operations/1"):
            sample = {"video": {"uri": "https://files.test/clip.mp4"}}
            return httpx.Response(200, json={"done": True, "response": {"generateVideoResponse": {"generatedSamples": [sample]}}}, request=request)
        return httpx.Response(200, content=b"mp4-bytes", request=request)

    with mock.patch.object(broll.httpx, "request", side_effect=respond), mock.patch.object(broll.time, "sleep"):
        clip = broll.generate("waves on a beach")

    assert (clip.video, clip.seconds, clip.cost) == (b"mp4-bytes", 8, Decimal("1.20"))


@override_settings(GEMINI_API_KEY="gemini-test")
def test_a_filtered_veo_prompt_is_a_clear_non_retryable_failure():
    def respond(method, url, **kwargs):
        return httpx.Response(200, json={"name": "operations/1", "done": True, "response": {}}, request=httpx.Request(method, url))

    with mock.patch.object(broll.httpx, "request", side_effect=respond), pytest.raises(AIProviderError) as raised:
        broll.generate("anything")

    assert raised.value.retryable is False and "filtered" in str(raised.value.detail)


# --- evaluation tracing ---------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(**LANGFUSE)
def test_a_run_traces_every_call_and_scores_each_lesson(creator, media):
    course = _awaiting_engine_video(creator)
    sent = []

    def ingest(url, json, auth, timeout):
        sent.append((url, auth, json["batch"]))
        return httpx.Response(207, request=httpx.Request("POST", url))

    with mock.patch("api.production.tracing.httpx.post", side_effect=ingest):
        run = _produce(course, creator)

    assert run.status == ProductionRunStatus.COMPLETED, run.status_reason
    assert {url for url, _, _ in sent} == {"https://langfuse.test/api/public/ingestion"}
    assert sent[0][1] == ("pk-test", "sk-test")
    events = [event for _, _, batch in sent for event in batch]
    trace = next(event for event in events if event["type"] == "trace-create")
    assert trace["body"]["id"] == str(run.id)
    generations = {event["body"]["name"] for event in events if event["type"] == "generation-create"}
    assert generations == {"storyboard", "narration", "caption_check", "visual_check"}
    scores = [event["body"] for event in events if event["type"] == "score-create"]
    accuracy = [score for score in scores if score["name"] == "caption_accuracy"]
    assert len(accuracy) == 3 and all(score["value"] == 100.0 for score in accuracy)  # two lessons and the trailer
    assert {score["name"] for score in scores} >= {"storyboard_first_try", "visual_check_passed", "loudness_lufs", "qc_attempts"}
    storyboard = next(event["body"] for event in events if event["body"].get("name") == "storyboard")
    assert storyboard["metadata"]["prompt_version"] and storyboard["usage"]["unit"] == "TOKENS"


@pytest.mark.django_db
@override_settings(**LANGFUSE)
def test_langfuse_being_down_never_fails_a_run(creator, media):
    course = _awaiting_engine_video(creator)

    with mock.patch("api.production.tracing.httpx.post", side_effect=httpx.ConnectError("down")):
        run = _produce(course, creator)

    assert run.status == ProductionRunStatus.COMPLETED, run.status_reason


@pytest.mark.django_db
def test_without_langfuse_keys_nothing_is_sent(creator, media):
    course = _awaiting_engine_video(creator)

    with mock.patch("api.production.tracing.httpx.post") as post:
        _produce(course, creator)

    post.assert_not_called()

"""Turns a storyboard into finished, checked video.

For each lesson: every scene is voiced and drawn (or filmed, for b-roll),
the scenes are assembled into one video at the loudness target, captions
are cut from the approved narration with the voice's own timing, the file
is measured, and a vision model looks at a frame of every scene. A lesson
reaches storage only once it passes the quality gate; one that fails is
made again with the next voice and fresh generated visuals, then fails the
run. Every call and every quality score is traced (api.production.tracing).

Every piece is stored under the hash of its inputs, so a retried run, or a
rework of one lesson, reuses whatever did not change.
"""

import logging
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from api.courses.ai.providers import AIProviderError
from api.operations.enums import CostCategory, PipelineStage, ProviderKind
from api.platform.services import platform_settings_service
from api.production.enums import AssetKind, ReworkAction, SceneType
from api.production.media import captions, ffmpeg, scoring, slides
from api.production.models import ProductionAsset, ProductionScene
from api.production.providers import broll, media_ai, visual_check
from api.production.providers.storyboard import split_sentences
from api.production.providers.voice import VoiceProvider
from api.production.services import asset_store
from api.production.services.run_ledger import RunLedger, text_cost

logger = logging.getLogger(__name__)

QC_ATTEMPTS = 2
"""A lesson failing the quality gate is made once more, with the next voice
(or a fresh take when there is only one)."""

TRAILER_MIN_SECONDS = 60
TRAILER_MAX_SECONDS = 120
TRAILER_MAX_WORDS = 250
"""About 105 seconds of speech: the trailer fits the 60-120 s preview rule."""

TRAILER_TAIL_SECONDS = 1.0
TRAILER_MAX_CARDS = 7

ENGINE_IMAGE_PROVIDER = "OpenAI Images"
BROLL_PROVIDER = "Veo"
TRANSCRIBE_PROVIDER = "OpenAI Transcription"
VISUAL_CHECK_PROVIDER = "OpenAI Vision"
RENDER_PROVIDER = "FFmpeg"

#: Spend recorded as VIDEO (visuals) rather than VOICE.
VISUAL_PROVIDERS = (ENGINE_IMAGE_PROVIDER, BROLL_PROVIDER)


class QualityCheckFailed(Exception):
    """A finished video failed the automated quality gate."""


@dataclass(frozen=True)
class Piece:
    """A stored narration or frame, with its local copy once fetched."""

    asset: ProductionAsset
    path: Path


# --- pronunciation ---------------------------------------------------------------


def apply_lexicon(text: str, lexicon: dict[str, str]) -> str:
    """What the voice is given: `text` with each term replaced by its spoken
    form (whole words, case-insensitive). Captions keep the original."""

    for term, spoken in sorted(lexicon.items(), key=lambda item: -len(item[0])):
        text = re.sub(rf"(?<!\w){re.escape(term)}(?!\w)", spoken, text, flags=re.IGNORECASE)
    return text


# --- narration -------------------------------------------------------------------


def _narrate(
    *,
    ledger: RunLedger,
    lesson,
    text: str,
    previous_text: str,
    next_text: str,
    voices: list[VoiceProvider],
    lexicon: dict[str, str],
    salt: str,
    workdir: Path,
    spent: dict,
) -> Piece:
    """Voice `text`, trying each voice in turn when one is unavailable."""

    spoken = apply_lexicon(text, lexicon)
    course = ledger.run.course
    last_error = None
    for voice in voices:
        key = asset_store.input_hash(
            course.id, "narration", voice.name, voice.voice, getattr(voice, "model", ""),
            spoken, previous_text[-300:], next_text[:300], salt,
        )
        path = workdir / f"{key}.mp3"
        cached = asset_store.find(key)
        if cached is not None:
            return Piece(cached, asset_store.fetch(cached.file_key, path))
        started = timezone.now()
        trace = {"name": "narration", "model": f"{voice.name}/{voice.voice}", "started_at": started, "input": spoken,
                 "metadata": {"lesson": lesson.title if lesson else "trailer"}}
        try:
            result = voice.synthesize(text=spoken, previous_text=previous_text, next_text=next_text)
        except AIProviderError as exc:
            logger.warning("voice %s failed for %s: %s", voice.name, lesson.id if lesson else "the trailer", exc.detail)
            ledger.tracer.generation(**trace, error=str(exc.detail))
            last_error = exc
            continue
        ledger.tracer.generation(**trace, usage={"input": len(spoken), "unit": "CHARACTERS"}, cost=result.cost)
        path.write_bytes(result.audio)
        seconds = ffmpeg.duration_seconds(path)
        alignment = result.alignment or captions.even_alignment(spoken, seconds)
        file_key = asset_store.put(path, course_id=course.id, name=key, content_type="audio/mpeg")
        asset = asset_store.record(
            key=key, kind=AssetKind.NARRATION, course=course, lesson=lesson, file_key=file_key,
            content_type="audio/mpeg", path=path, duration_ms=int(seconds * 1000),
            data={"spoken": spoken, "alignment": alignment, "voice": voice.name},
        )
        spent[voice.name] = spent.get(voice.name, Decimal("0")) + result.cost
        return Piece(asset, path)
    raise last_error


# --- visuals -----------------------------------------------------------------------


def _draw(*, ledger: RunLedger, lesson, scene: ProductionScene, workdir: Path, spent: dict, salt: str = "") -> Piece:
    """The scene's visual: a motion clip for a BROLL scene (when b-roll is
    configured), otherwise a drawn frame. Generated visuals take `salt`, so
    a retake after a failed visual check asks for new ones."""

    course = ledger.run.course
    generative = scene.scene_type in (SceneType.IMAGE, SceneType.BROLL)
    key = asset_store.input_hash(
        course.id, "slide", slides.TEMPLATE_VERSION, scene.scene_type, lesson.title,
        scene.on_screen_text, scene.bullets, scene.code, scene.narration,
        scene.visual_brief if generative else "", salt if generative else "",
    )
    cached = asset_store.find(key)
    if cached is not None:
        suffix = ".mp4" if cached.content_type == "video/mp4" else ".png"
        return Piece(cached, asset_store.fetch(cached.file_key, workdir / f"{key}{suffix}"))
    if scene.scene_type == SceneType.BROLL and scene.visual_brief and broll.is_configured():
        started = timezone.now()
        try:
            clip = broll.generate(scene.visual_brief)
        except AIProviderError as exc:
            # Footage is a nice-to-have: the scene is drawn as a still instead.
            logger.warning("b-roll failed for lesson %s: %s", lesson.id, exc.detail)
            ledger.tracer.generation(name="broll", model=settings.VEO_MODEL, started_at=started, input=scene.visual_brief, error=str(exc.detail))
        else:
            ledger.tracer.generation(
                name="broll", model=settings.VEO_MODEL, started_at=started, input=scene.visual_brief,
                usage={"output": clip.seconds, "unit": "SECONDS"}, cost=clip.cost,
            )
            spent[BROLL_PROVIDER] = spent.get(BROLL_PROVIDER, Decimal("0")) + clip.cost
            path = workdir / f"{key}.mp4"
            path.write_bytes(clip.video)
            file_key = asset_store.put(path, course_id=course.id, name=key, content_type="video/mp4")
            asset = asset_store.record(
                key=key, kind=AssetKind.BROLL, course=course, lesson=lesson, file_key=file_key,
                content_type="video/mp4", path=path, duration_ms=clip.seconds * 1000,
            )
            return Piece(asset, path)
    illustration = None
    if generative and scene.visual_brief:
        started = timezone.now()
        try:
            illustration, cost = media_ai.illustrate(scene.visual_brief)
            spent[ENGINE_IMAGE_PROVIDER] = spent.get(ENGINE_IMAGE_PROVIDER, Decimal("0")) + cost
            ledger.tracer.generation(
                name="illustration", model=settings.OPENAI_IMAGE_MODEL, started_at=started, input=scene.visual_brief,
                usage={"output": 1, "unit": "IMAGES"}, cost=cost,
            )
        except AIProviderError as exc:
            # A scene without its picture still teaches: it falls back to text.
            logger.warning("illustration failed for lesson %s: %s", lesson.id, exc.detail)
            ledger.tracer.generation(name="illustration", model=settings.OPENAI_IMAGE_MODEL, started_at=started, input=scene.visual_brief, error=str(exc.detail))
    path = workdir / f"{key}.png"
    path.write_bytes(
        slides.render_scene(
            scene_type=SceneType.IMAGE if scene.scene_type == SceneType.BROLL else scene.scene_type,
            lesson_title=lesson.title,
            on_screen_text=scene.on_screen_text,
            bullets=list(scene.bullets),
            code=scene.code,
            narration=scene.narration,
            illustration=illustration,
        )
    )
    file_key = asset_store.put(path, course_id=course.id, name=key, content_type="image/png")
    asset = asset_store.record(
        key=key, kind=AssetKind.SLIDE, course=course, lesson=lesson, file_key=file_key,
        content_type="image/png", path=path,
    )
    return Piece(asset, path)


# --- quality gate ------------------------------------------------------------------


def quality_failures(
    measured: ffmpeg.Measurements, *, accuracy: Decimal, silence_allowance: float = 0.0
) -> list[str]:
    """Why a finished video may not go to review; empty when it passes."""

    settings_row = platform_settings_service.get_settings()
    failures = []
    if (measured.width, measured.height) != (1920, 1080):
        failures.append(f"resolution {measured.width}x{measured.height}, expected 1920x1080")
    if abs(measured.fps - ffmpeg.FPS) > 0.01:
        failures.append(f"{measured.fps} fps, expected {ffmpeg.FPS}")
    if (measured.video_codec, measured.audio_codec) != ("h264", "aac"):
        failures.append(f"codecs {measured.video_codec}/{measured.audio_codec}, expected h264/aac")
    if abs(measured.integrated_lufs - ffmpeg.TARGET_LUFS) > ffmpeg.LOUDNESS_TOLERANCE_LU:
        failures.append(f"loudness {measured.integrated_lufs} LUFS, expected {ffmpeg.TARGET_LUFS}")
    if measured.drift_ms > settings_row.production_max_av_drift_ms:
        failures.append(f"audio/video drift {measured.drift_ms} ms")
    if measured.black_seconds > 0:
        failures.append(f"{measured.black_seconds} s of black frames")
    if measured.longest_silence_seconds >= ffmpeg.SILENCE_MIN_SECONDS + silence_allowance:
        failures.append(f"{measured.longest_silence_seconds} s of silence")
    if accuracy < settings_row.production_min_caption_accuracy:
        failures.append(
            f"caption accuracy {accuracy}%, minimum {settings_row.production_min_caption_accuracy}%"
        )
    return failures


def _visual_problems(*, ledger: RunLedger, lesson, video: Path, scenes_at: list[tuple[float, ProductionScene]], workdir: Path) -> list[str]:
    """Frame-by-frame check of the rendered lesson: one frame from the middle
    of every scene, judged by a vision model against what the scene should
    show. Returns one failure line per scene with problems."""

    started = timezone.now()
    frames = [
        visual_check.Frame(
            jpeg=ffmpeg.frame_at(source=video, seconds=moment, out=workdir / f"{video.stem}.{index}.jpg"),
            scene_type=scene.scene_type,
            on_screen_text=scene.on_screen_text,
            bullets=list(scene.bullets),
        )
        for index, (moment, scene) in enumerate(scenes_at)
    ]
    try:
        verdict = visual_check.check(frames)
    except AIProviderError as exc:
        ledger.tracer.generation(name="visual_check", model=settings.OPENAI_TEXT_MODEL, started_at=started, error=str(exc.detail))
        raise
    cost = text_cost(verdict.input_tokens, verdict.output_tokens)
    ledger.charge(
        category=CostCategory.TEXT, amount=cost, provider=ledger.provider(VISUAL_CHECK_PROVIDER, ProviderKind.TEXT),
        note=f"Visual check: {lesson.title}",
    )
    ledger.tracer.generation(
        name="visual_check", model=settings.OPENAI_TEXT_MODEL, started_at=started,
        input={"lesson": lesson.title, "frames": len(frames)}, output=verdict.problems,
        usage={"input": verdict.input_tokens, "output": verdict.output_tokens, "unit": "TOKENS"}, cost=cost,
        metadata={"prompt_version": visual_check.VISUAL_CHECK_PROMPT_VERSION},
    )
    return [
        f"visual check, scene {index + 1} ({scenes_at[index][1].scene_type}): {'; '.join(problems)}"
        for index, problems in sorted(verdict.problems.items())
    ]


def _check(
    *, ledger: RunLedger, lesson, video: Path, transcript: str, workdir: Path,
    silence_allowance: float = 0.0, scenes_at: list[tuple[float, ProductionScene]] | None = None,
):
    """Measure `video`, score its captions and, for a lesson with the visual
    check on, look at every scene; raise QualityCheckFailed."""

    started = timezone.now()
    measured = ffmpeg.measure(video)
    speech = workdir / f"{video.stem}.speech.mp3"
    ffmpeg.extract_speech(source=video, out=speech)
    heard_at = timezone.now()
    heard, cost = media_ai.transcribe(speech.read_bytes(), seconds=measured.duration_seconds)
    transcriber = ledger.provider(TRANSCRIBE_PROVIDER, ProviderKind.TEXT)
    ledger.charge(category=CostCategory.TEXT, amount=cost, provider=transcriber, note=f"Caption check: {video.stem[:40]}")
    ledger.tracer.generation(
        name="caption_check", model=settings.PRODUCTION_TRANSCRIBE_MODEL, started_at=heard_at,
        output=heard, usage={"input": round(measured.duration_seconds), "unit": "SECONDS"}, cost=cost,
    )
    accuracy = scoring.caption_accuracy_percent(transcript, heard)
    failures = quality_failures(measured, accuracy=accuracy, silence_allowance=silence_allowance)
    visual_failures = []
    if scenes_at and platform_settings_service.get_settings().production_visual_check_enabled:
        visual_failures = _visual_problems(ledger=ledger, lesson=lesson, video=video, scenes_at=scenes_at, workdir=workdir)
        ledger.tracer.score(name="visual_check_passed", value=0 if visual_failures else 1, comment=lesson.title)
    failures += visual_failures
    subject = lesson.title if lesson else "trailer"
    ledger.tracer.score(name="caption_accuracy", value=float(accuracy), comment=subject)
    ledger.tracer.score(name="loudness_lufs", value=measured.integrated_lufs, comment=subject)
    ledger.tracer.score(name="av_drift_ms", value=measured.drift_ms, comment=subject)
    ledger.step(
        stage=PipelineStage.AUTO_QA, step="quality_check", started_at=started, lesson=lesson,
        provider=transcriber, error="; ".join(failures),
    )
    if failures:
        raise QualityCheckFailed("; ".join(failures))
    return measured, accuracy


# --- assembly ------------------------------------------------------------------------


def _publish_video(
    *, ledger: RunLedger, key: str, kind: str, lesson, video: Path, cues, transcript: str,
    measured, accuracy, voice: str, workdir: Path,
) -> ProductionAsset:
    course = ledger.run.course
    srt = workdir / f"{key}.srt"
    srt.write_text(captions.to_srt(cues))
    vtt = workdir / f"{key}.vtt"
    vtt.write_text(captions.to_vtt(cues))
    text = workdir / f"{key}.txt"
    text.write_text(transcript)
    data = {
        "srt_key": asset_store.put(srt, course_id=course.id, name=key, content_type="application/x-subrip"),
        "vtt_key": asset_store.put(vtt, course_id=course.id, name=key, content_type="text/vtt"),
        "transcript_key": asset_store.put(text, course_id=course.id, name=key, content_type="text/plain"),
        "measurements": measured.as_dict(),
        "caption_accuracy_percent": str(accuracy),
        "voice": voice,
    }
    file_key = asset_store.put(video, course_id=course.id, name=key, content_type="video/mp4")
    return asset_store.record(
        key=key, kind=kind, course=course, lesson=lesson, file_key=file_key, content_type="video/mp4",
        path=video, duration_ms=int(measured.duration_seconds * 1000), data=data,
    )


def _charge_voices(ledger: RunLedger, spent: dict, *, lesson_title: str) -> None:
    for name, amount in spent.items():
        category = CostCategory.VIDEO if name in VISUAL_PROVIDERS else CostCategory.VOICE
        kind = ProviderKind.VIDEO if name in VISUAL_PROVIDERS else ProviderKind.VOICE
        ledger.charge(category=category, amount=amount, provider=ledger.provider(name, kind), note=f"{name}: {lesson_title}")
    spent.clear()


def produce_lesson(
    *,
    ledger: RunLedger,
    lesson,
    scenes: list[ProductionScene],
    actions: list[str],
    voices: list[VoiceProvider],
    lexicon: dict[str, str],
    workdir: Path,
) -> ProductionAsset:
    """The lesson's finished video asset: reused when nothing that goes into
    it changed, otherwise voiced, drawn, assembled, checked and stored."""

    run_salt = str(ledger.run.id)
    voice_salt = run_salt if ReworkAction.REVOICE in actions else ""
    render_salt = run_salt if ReworkAction.RERENDER in actions else ""
    last_failure: QualityCheckFailed | None = None
    for attempt in range(QC_ATTEMPTS):
        # A retake moves to the next voice; with one voice, a fresh take of it.
        chain = voices[attempt:] or voices
        take = f"{voice_salt}take{attempt}" if attempt and len(voices) == 1 else voice_salt
        spent: dict = {}

        started = timezone.now()
        narrations = []
        for index, scene in enumerate(scenes):
            narrations.append(
                _narrate(
                    ledger=ledger, lesson=lesson, text=scene.narration,
                    previous_text=scenes[index - 1].narration if index else "",
                    next_text=scenes[index + 1].narration if index + 1 < len(scenes) else "",
                    voices=chain, lexicon=lexicon, salt=take, workdir=workdir, spent=spent,
                )
            )
        voice_names = {piece.asset.data.get("voice", "") for piece in narrations}
        if spent:
            ledger.step(
                stage=PipelineStage.MEDIA_PRODUCTION, step="narration", started_at=started, lesson=lesson,
                provider=ledger.provider(sorted(voice_names)[0]),
            )

        started = timezone.now()
        # A retake also asks for new illustrations and clips, in case the
        # visual check was what failed.
        visual_salt = f"take{attempt}" if attempt else ""
        frames = [
            _draw(ledger=ledger, lesson=lesson, scene=scene, workdir=workdir, spent=spent, salt=visual_salt)
            for scene in scenes
        ]
        ledger.step(stage=PipelineStage.MEDIA_PRODUCTION, step="visuals", started_at=started, lesson=lesson)
        _charge_voices(ledger, spent, lesson_title=lesson.title)

        key = asset_store.input_hash(
            ledger.run.course.id, "lesson_video", ffmpeg.RENDER_VERSION,
            [piece.asset.key for piece in narrations], [piece.asset.key for piece in frames], render_salt,
        )
        cached = asset_store.find(key)
        if cached is not None:
            return cached

        started = timezone.now()
        parts, held, cues, scenes_at, offset = [], [], [], [], 0.0
        for scene, narration, frame in zip(scenes, narrations, frames, strict=True):
            wav = workdir / f"{narration.asset.key}.wav"
            seconds = ffmpeg.to_wav(source=narration.path, out=wav, pause_seconds=ffmpeg.SCENE_PAUSE_SECONDS)
            parts.append(wav)
            held.append((frame.path, seconds))
            scenes_at.append((offset + seconds / 2, scene))
            cues += captions.cues_for(
                text=scene.narration, spoken=narration.asset.data["spoken"],
                alignment=narration.asset.data["alignment"], offset=offset,
            )
            offset += seconds
        audio = workdir / f"{key}.wav"
        ffmpeg.join_wavs(parts=parts, out=audio)
        video = workdir / f"{key}.mp4"
        ffmpeg.assemble(frames=held, audio=audio, out=video, title=lesson.title)
        ledger.step(
            stage=PipelineStage.MEDIA_PRODUCTION, step="render", started_at=started, lesson=lesson,
            provider=ledger.provider(RENDER_PROVIDER, ProviderKind.VIDEO),
        )

        transcript = "\n\n".join(scene.narration for scene in scenes)
        try:
            measured, accuracy = _check(
                ledger=ledger, lesson=lesson, video=video, transcript=transcript, workdir=workdir, scenes_at=scenes_at
            )
        except QualityCheckFailed as exc:
            last_failure = exc
            ledger.checkpoint()
            continue
        asset = _publish_video(
            ledger=ledger, key=key, kind=AssetKind.LESSON_VIDEO, lesson=lesson, video=video, cues=cues,
            transcript=transcript, measured=measured, accuracy=accuracy, voice=", ".join(sorted(voice_names)),
            workdir=workdir,
        )
        ledger.tracer.score(name="qc_attempts", value=attempt + 1, comment=lesson.title)
        ledger.checkpoint()
        return asset
    ledger.tracer.score(name="qc_attempts", value=QC_ATTEMPTS + 1, comment=f"{lesson.title} (failed)")
    raise QualityCheckFailed(f"Lesson '{lesson.title}' failed the quality check: {last_failure}")


# --- trailer and thumbnail ------------------------------------------------------------


def trailer_script(course) -> str:
    """The trailer's narration, from approved text only: the course
    description, then its learning objectives, in whole sentences up to
    TRAILER_MAX_WORDS."""

    sentences = split_sentences(course.description or "")
    objectives = [str(item).strip().rstrip(".") + "." for item in course.learning_objectives or [] if str(item).strip()]
    chosen, words = [], 0
    for sentence in sentences + objectives:
        count = len(sentence.split())
        if words + count > TRAILER_MAX_WORDS:
            break
        chosen.append(sentence)
        words += count
    return " ".join(chosen) or course.title


def produce_trailer(*, ledger: RunLedger, voices: list[VoiceProvider], lexicon: dict, workdir: Path) -> ProductionAsset:
    """A 60-120 s faceless trailer: title and module cards over narration of
    the course's own description and objectives."""

    course = ledger.run.course
    script = trailer_script(course)
    spent: dict = {}
    started = timezone.now()
    narration = _narrate(
        ledger=ledger, lesson=None, text=script, previous_text="", next_text="", voices=voices,
        lexicon=lexicon, salt="trailer", workdir=workdir, spent=spent,
    )
    modules = list(course.modules.order_by("order").values_list("title", flat=True))[: TRAILER_MAX_CARDS - 1]
    cards = [(course.title, course.category.name if course.category_id else "")] + [
        (title, f"Module {index}") for index, title in enumerate(modules, start=1)
    ]
    card_pieces = []
    for heading, subtitle in cards:
        key = asset_store.input_hash(course.id, "card", slides.TEMPLATE_VERSION, heading, subtitle)
        path = workdir / f"{key}.png"
        cached = asset_store.find(key)
        if cached is None:
            path.write_bytes(slides.render_card(heading=heading, subtitle=subtitle))
            file_key = asset_store.put(path, course_id=course.id, name=key, content_type="image/png")
            cached = asset_store.record(key=key, kind=AssetKind.SLIDE, course=course, file_key=file_key, content_type="image/png", path=path)
        else:
            asset_store.fetch(cached.file_key, path)
        card_pieces.append(Piece(cached, path))
    _charge_voices(ledger, spent, lesson_title="Trailer")

    key = asset_store.input_hash(
        course.id, "trailer", ffmpeg.RENDER_VERSION, narration.asset.key, [piece.asset.key for piece in card_pieces],
    )
    cached = asset_store.find(key)
    if cached is not None:
        return cached

    spoken_seconds = narration.asset.duration_ms / 1000
    if spoken_seconds > TRAILER_MAX_SECONDS:
        raise QualityCheckFailed(f"The trailer narration runs {spoken_seconds:.0f} s, over {TRAILER_MAX_SECONDS} s.")
    total = min(max(spoken_seconds + TRAILER_TAIL_SECONDS, TRAILER_MIN_SECONDS), TRAILER_MAX_SECONDS)
    audio = workdir / f"{key}.wav"
    ffmpeg.to_wav(source=narration.path, out=audio, pause_seconds=max(total - spoken_seconds, 0))
    per_card = total / len(card_pieces)
    video = workdir / f"{key}.mp4"
    ffmpeg.assemble(frames=[(piece.path, per_card) for piece in card_pieces], audio=audio, out=video, title=course.title)
    ledger.step(stage=PipelineStage.PREVIEW_VIDEO, step="render", started_at=started, provider=ledger.provider(RENDER_PROVIDER, ProviderKind.VIDEO))
    cues = captions.cues_for(text=script, spoken=narration.asset.data["spoken"], alignment=narration.asset.data["alignment"], offset=0.0)
    # The padding after a short narration is intended silence.
    measured, accuracy = _check(
        ledger=ledger, lesson=None, video=video, transcript=script, workdir=workdir,
        silence_allowance=max(total - spoken_seconds, 0),
    )
    return _publish_video(
        ledger=ledger, key=key, kind=AssetKind.TRAILER, lesson=None, video=video, cues=cues, transcript=script,
        measured=measured, accuracy=accuracy, voice=narration.asset.data.get("voice", ""), workdir=workdir,
    )


def produce_thumbnail(*, ledger: RunLedger, workdir: Path) -> ProductionAsset:
    course = ledger.run.course
    subtitle = course.category.name if course.category_id else ""
    key = asset_store.input_hash(course.id, "thumbnail", slides.TEMPLATE_VERSION, course.title, subtitle)
    cached = asset_store.find(key)
    if cached is not None:
        return cached
    path = workdir / f"{key}.png"
    path.write_bytes(slides.render_card(heading=course.title, subtitle=subtitle, size=slides.THUMBNAIL_SIZE))
    file_key = asset_store.put(path, course_id=course.id, name=key, content_type="image/png")
    return asset_store.record(key=key, kind=AssetKind.THUMBNAIL, course=course, file_key=file_key, content_type="image/png", path=path)

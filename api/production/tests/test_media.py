"""The media building blocks: caption timing, caption accuracy, the quality
gate on deliberately broken files, pronunciation, and scene frames."""

import io
import subprocess
from decimal import Decimal

import pytest
from PIL import Image

from api.production.enums import SceneType
from api.production.media import captions, ffmpeg, scoring, slides
from api.production.services import lesson_video_service
from api.production.services.lesson_video_service import apply_lexicon, quality_failures


def _clip(path, *, video: str, audio: str, seconds: float = 4.0):
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", f"{video}:size=1920x1080:rate=30:duration={seconds}",
            "-f", "lavfi", "-i", f"{audio}:duration={seconds}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-shortest", str(path),
        ],
        check=True,
    )
    return path


# --- captions ---------------------------------------------------------------------


def test_cues_follow_the_voices_timing_and_offset():
    text = "First sentence. Second one."
    alignment = [[index * 0.1, index * 0.1 + 0.1] for index in range(len(text))]

    cues = captions.cues_for(text=text, spoken=text, alignment=alignment, offset=10.0)

    assert [cue.text for cue in cues] == ["First sentence.", "Second one."]
    assert cues[0].start == pytest.approx(10.0) and cues[0].end == pytest.approx(11.5)
    assert cues[1].start == pytest.approx(11.6)


def test_long_sentences_split_at_words_and_wrap_to_two_lines():
    text = " ".join(["word"] * 40) + "."
    cues = captions.cues_for(text=text, spoken=text, alignment=captions.even_alignment(text, 10.0), offset=0)

    assert len(cues) > 1
    for cue in cues:
        lines = cue.text.split("\n")
        assert len(lines) <= 2 and all(len(line) <= captions.LINE_MAX_CHARS + 5 for line in lines)
        assert len(cue.text.replace("\n", " ")) <= captions.CUE_MAX_CHARS


def test_captions_keep_the_approved_spelling_when_the_voice_was_given_a_respelling():
    text = "Kubernetes runs pods."
    spoken = apply_lexicon(text, {"kubernetes": "koo-ber-NET-eez"})

    cues = captions.cues_for(text=text, spoken=spoken, alignment=captions.even_alignment(spoken, 3.0), offset=0)

    assert spoken == "koo-ber-NET-eez runs pods."
    assert cues[0].text == "Kubernetes runs pods."
    assert cues[0].end == pytest.approx(3.0, abs=0.2)


def test_lexicon_matches_whole_words_only():
    assert apply_lexicon("SQL and SQLite", {"SQL": "sequel"}) == "sequel and SQLite"


def test_srt_and_vtt_formats():
    cues = [captions.Cue(0.0, 1.5, "Hello"), captions.Cue(3661.25, 3662.0, "Later")]

    assert captions.to_srt(cues) == "1\n00:00:00,000 --> 00:00:01,500\nHello\n\n2\n01:01:01,250 --> 01:01:02,000\nLater\n"
    assert captions.to_vtt(cues).startswith("WEBVTT\n\n00:00:00.000 --> 00:00:01.500\nHello\n")


# --- caption accuracy ------------------------------------------------------------------


def test_caption_accuracy_is_one_minus_the_word_error_rate():
    assert scoring.caption_accuracy_percent("The cat sat.", "the cat sat") == Decimal("100.00")
    assert scoring.caption_accuracy_percent("one two three four", "one two tree four") == Decimal("75.00")
    assert scoring.caption_accuracy_percent("one two", "") == Decimal("0.00")


# --- the quality gate on real files ------------------------------------------------------


@pytest.mark.django_db
def test_a_normalised_file_passes_and_each_broken_file_fails_for_its_reason(tmp_path):
    good = tmp_path / "good.mp4"
    tone = _clip(tmp_path / "tone.mp4", video="color=c=white", audio="sine=frequency=440")
    audio = tmp_path / "tone.wav"
    ffmpeg.to_wav(source=tone, out=audio)
    frame = tmp_path / "frame.png"
    frame.write_bytes(slides.render_card(heading="Good", subtitle=""))
    ffmpeg.assemble(frames=[(frame, 4.0)], audio=audio, out=good, title="Good")
    assert quality_failures(ffmpeg.measure(good), accuracy=Decimal("100")) == []

    quiet = _clip(tmp_path / "quiet.mp4", video="color=c=white", audio="aevalsrc=0.001*sin(2*PI*440*t):s=48000")
    black = _clip(tmp_path / "black.mp4", video="color=c=black", audio="sine=frequency=440")
    silent = _clip(tmp_path / "silent.mp4", video="color=c=white", audio="anullsrc=r=48000:cl=stereo")
    small = tmp_path / "small.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(good), "-vf", "scale=1280:720", "-c:a", "copy", str(small)], check=True)

    assert any("loudness" in reason for reason in quality_failures(ffmpeg.measure(quiet), accuracy=Decimal("100")))
    assert any("black frames" in reason for reason in quality_failures(ffmpeg.measure(black), accuracy=Decimal("100")))
    assert any("silence" in reason for reason in quality_failures(ffmpeg.measure(silent), accuracy=Decimal("100")))
    assert any("resolution 1280x720" in reason for reason in quality_failures(ffmpeg.measure(small), accuracy=Decimal("100")))
    assert any("caption accuracy" in reason for reason in quality_failures(ffmpeg.measure(good), accuracy=Decimal("80")))


@pytest.mark.django_db
def test_the_drift_and_accuracy_limits_are_the_admins_settings():
    from api.production.tests.conftest import set_settings

    measured = ffmpeg.Measurements(
        width=1920, height=1080, fps=30.0, video_codec="h264", audio_codec="aac", audio_rate=48000,
        duration_seconds=10.0, drift_ms=150, integrated_lufs=-16.0, true_peak_dbtp=-2.0,
        black_seconds=0.0, longest_silence_seconds=0.0,
    )
    assert quality_failures(measured, accuracy=Decimal("96")) == ["audio/video drift 150 ms"]

    set_settings(production_max_av_drift_ms=200, production_min_caption_accuracy=Decimal("97"))

    assert quality_failures(measured, accuracy=Decimal("96")) == ["caption accuracy 96%, minimum 97.00%"]


# --- frames and the trailer script ---------------------------------------------------------


@pytest.mark.parametrize("scene_type", SceneType.values)
def test_every_scene_type_draws_a_full_hd_frame(scene_type):
    png = slides.render_scene(
        scene_type=scene_type,
        lesson_title="Containers",
        on_screen_text="Images are layered",
        bullets=["Build | Ship", "Layers are cached", "Run anywhere"],
        code="docker run nginx",
        narration="Containers package an app. They run anywhere.",
    )
    assert Image.open(io.BytesIO(png)).size == (1920, 1080)


def test_the_thumbnail_is_a_1280_by_720_card():
    png = slides.render_card(heading="Intro to Python", subtitle="Data", size=slides.THUMBNAIL_SIZE)
    assert Image.open(io.BytesIO(png)).size == (1280, 720)


def test_the_trailer_reads_only_approved_text_within_the_word_limit():
    class Course:
        title = "Intro"
        description = "First sentence. " + "Second sentence is long. " * 200
        learning_objectives = ["Write code"]

    script = lesson_video_service.trailer_script(Course())

    assert script.startswith("First sentence.")
    assert len(script.split()) <= lesson_video_service.TRAILER_MAX_WORDS
    assert set(scoring.words(script)) <= set(scoring.words(Course.description + " Write code."))

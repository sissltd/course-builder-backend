"""Fakes for the Production Engine's vendors.

Only the vendors are fake: narration is a real tone made by ffmpeg, files go
to a directory standing in for object storage, and the transcription vendor
either hears the narration perfectly or hears something else. Rendering,
measuring, the quality gate and delivery all run for real.
"""

import shutil
import subprocess
from decimal import Decimal
from unittest import mock

import pytest

from api.courses.ai.providers import AIProviderError
from api.platform.services import platform_settings_service
from api.production.providers import media_ai, visual_check
from api.production.providers.storyboard import PlannedScene, StoryboardResult
from api.production.providers.voice import VoiceProvider, VoiceResult
from api.production.services import lesson_video_service
from shared.services.storage_service import StorageService

BUCKET = "https://bucket.test"
SIGNED = "https://signed.test"
TONE_SECONDS = 1.0


def set_settings(**fields):
    row = platform_settings_service.get_settings()
    for name, value in fields.items():
        setattr(row, name, value)
    row.save()


@pytest.fixture(scope="session")
def tone(tmp_path_factory) -> bytes:
    path = tmp_path_factory.mktemp("tone") / "tone.mp3"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=330:duration={TONE_SECONDS}", "-c:a", "libmp3lame", str(path)],
        check=True,
    )
    return path.read_bytes()


def key_of(value: str) -> str:
    """The storage key behind a key, a public URL or a signed link."""

    for prefix in (f"{BUCKET}/", f"{SIGNED}/"):
        if value.startswith(prefix):
            return value[len(prefix) :].split("?")[0]
    return value


class Bucket:
    def __init__(self, root):
        self.root = root

    def path(self, value: str):
        return self.root / key_of(value)

    def read_text(self, value: str) -> str:
        return self.path(value).read_text()


@pytest.fixture
def store(tmp_path):
    """Object storage as a directory. Signed links name the key, so tests
    can read back what a link points at."""

    root = tmp_path / "bucket"

    def upload_file(path, *, file_key, content_type):
        target = root / file_key
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        return file_key

    def download_file(file_key, path):
        shutil.copyfile(root / key_of(file_key), path)

    def public_url(file_key):
        return f"{BUCKET}/{file_key.lstrip('/')}"

    def presign(file_key, expires_in=600):
        return f"{SIGNED}/{key_of(file_key)}?expires={expires_in}" if file_key else None

    with (
        mock.patch.object(StorageService, "upload_file", upload_file),
        mock.patch.object(StorageService, "download_file", download_file),
        mock.patch.object(StorageService, "public_url", public_url),
        mock.patch.object(StorageService, "generate_presigned_get", presign),
    ):
        root.mkdir()
        yield Bucket(root)


class FakeVoice(VoiceProvider):
    """Speaks a one-second tone for any text; remembers what it was given.
    `failing` makes it refuse texts containing that word."""

    def __init__(self, name: str, tone: bytes):
        self.name = name
        self.voice = f"{name.lower()}-stock"
        self.tone = tone
        self.calls: list[str] = []
        self.failing: str | None = None

    def synthesize(self, *, text, previous_text, next_text):
        if self.failing and self.failing in text:
            raise AIProviderError(detail=f"{self.name} refused", retryable=False)
        self.calls.append(text)
        return VoiceResult(audio=self.tone, alignment=None, cost=Decimal("0.01"))


@pytest.fixture
def voices(tone):
    primary, fallback = FakeVoice("ElevenLabs", tone), FakeVoice("Google TTS", tone)
    with mock.patch("api.production.services.production_service.get_voice_providers", return_value=[primary, fallback]):
        yield primary, fallback


class Hearing:
    """The transcription vendor: hears the narration perfectly, or not."""

    def __init__(self):
        self.garbled = False
        self.calls = 0


@pytest.fixture
def hearing():
    state = Hearing()
    real_check = lesson_video_service._check

    def check(**kwargs):
        state.calls += 1
        heard = "nothing like the script at all" if state.garbled else kwargs["transcript"]
        with mock.patch.object(media_ai, "transcribe", return_value=(heard, Decimal("0.001"))):
            return real_check(**kwargs)

    with mock.patch.object(lesson_video_service, "_check", check):
        yield state


class OneScenePerLesson:
    """Storyboards a whole lesson as one scene (key points unless told
    otherwise). Counts calls."""

    def __init__(self):
        self.calls: list[str] = []
        self.scene_type = "BULLETS"
        self.broll_limits: list[int] = []

    def plan_lesson(self, *, course_title, lesson_title, sentences, broll_limit=0):
        self.calls.append(lesson_title)
        self.broll_limits.append(broll_limit)
        return StoryboardResult(
            scenes=[PlannedScene(0, len(sentences) - 1, self.scene_type, "Key point", "A server rack", ("First point", "Second point"))],
            input_tokens=1000,
            output_tokens=200,
        )


@pytest.fixture
def storyboard():
    fake = OneScenePerLesson()
    with mock.patch("api.production.services.production_service.get_storyboard_provider", return_value=fake):
        yield fake


class Eyes:
    """The vision model of the visual check: sees nothing wrong, or the
    problems it is given (for every frame)."""

    def __init__(self):
        self.problems: list[str] = []
        self.frames_seen = 0

    def check(self, frames):
        self.frames_seen += len(frames)
        problems = {index: list(self.problems) for index in range(len(frames))} if self.problems else {}
        return visual_check.Verdict(problems=problems, input_tokens=500, output_tokens=50)


@pytest.fixture
def eyes():
    fake = Eyes()
    with mock.patch.object(visual_check, "check", fake.check):
        yield fake


@pytest.fixture
def no_dispatch():
    with mock.patch("api.production.tasks.run_production.delay") as delay:
        yield delay

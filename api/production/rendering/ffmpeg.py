"""ffmpeg and ffprobe: scene rendering, assembly, loudness and measurement.

Every lesson video is built the same way: each scene's frame is held for
its narration plus a short pause, the scenes are joined, and the audio is
normalised to the e-learning loudness target in two passes. The finished
file is then measured, and those measurements are the evidence the QA seat
reads.
"""

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

RENDER_VERSION = "render-1"
"""Part of every lesson video's hash: changing the encode below must change this."""

FPS = 30
AUDIO_RATE = 48_000
SCENE_PAUSE_SECONDS = 0.5

TARGET_LUFS = -16.0
"""Integrated loudness target for spoken e-learning audio (EBU R128 family
practice for online speech; podcasts and YouTube sit at -14 to -16)."""

TRUE_PEAK_DBTP = -1.0
LOUDNESS_RANGE_LU = 11.0
LOUDNESS_TOLERANCE_LU = 1.0
"""How far the measured loudness may sit from the target."""

BLACK_MIN_SECONDS = 2.0
SILENCE_MIN_SECONDS = 3.0
SILENCE_THRESHOLD_DB = -50

COMMAND_TIMEOUT_SECONDS = 30 * 60

DISCLOSURE = "Narration is AI-generated (synthetic voice). Produced by the SoluDesk Production Engine."
"""Written into every file's metadata: the machine-readable AI disclosure."""


class MediaError(Exception):
    """ffmpeg or ffprobe failed. Not retryable: the same inputs fail again."""


def run(args: list[str]) -> str:
    """Run ffmpeg/ffprobe; return stderr (ffmpeg's reports go there)."""

    try:
        completed = subprocess.run(  # noqa: S603 - fixed binaries, no shell
            args, capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MediaError(f"{args[0]} could not run: {exc}") from exc
    if completed.returncode != 0:
        raise MediaError(f"{args[0]} failed: {completed.stderr.strip()[-400:]}")
    return completed.stderr + completed.stdout


def probe(path: Path) -> dict:
    output = run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)]
    )
    return json.loads(output[output.index("{") :])


def duration_seconds(path: Path) -> float:
    return float(probe(path)["format"]["duration"])


def to_wav(*, source: Path, out: Path, pause_seconds: float = 0.0) -> float:
    """Decode narration to 48 kHz stereo PCM with `pause_seconds` of silence
    after it; returns the exact length in seconds. PCM segments join
    sample-exactly, so scene timings and captions never drift."""

    run(
        [
            "ffmpeg", "-y", "-v", "error", "-i", str(source),
            "-af", f"aresample={AUDIO_RATE},aformat=channel_layouts=stereo,apad=pad_dur={pause_seconds}",
            "-c:a", "pcm_s16le", str(out),
        ]
    )
    return duration_seconds(out)


def silence_wav(*, seconds: float, out: Path) -> None:
    run(
        [
            "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"anullsrc=r={AUDIO_RATE}:cl=stereo",
            "-t", f"{seconds:.3f}", "-c:a", "pcm_s16le", str(out),
        ]
    )


def join_wavs(*, parts: list[Path], out: Path) -> None:
    listing = out.with_suffix(".txt")
    listing.write_text("".join(f"file '{part.resolve()}'\n" for part in parts))
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(out)])


def _loudness(audio: Path) -> dict:
    target = f"I={TARGET_LUFS}:TP={TRUE_PEAK_DBTP}:LRA={LOUDNESS_RANGE_LU}"
    report = run(
        ["ffmpeg", "-v", "info", "-i", str(audio), "-vn", "-af", f"loudnorm={target}:print_format=json", "-f", "null", "-"]
    )
    return json.loads(report[report.rindex("{") : report.rindex("}") + 1])


def assemble(*, frames: list[tuple[Path, float]], audio: Path, out: Path, title: str) -> None:
    """Encode the video once: each frame (a still, or a motion clip looped or
    cut to fit) held for its seconds, over `audio` normalised to TARGET_LUFS
    in two passes (the first measures, this one applies), laid out for the
    web (`+faststart`), with the disclosure in the metadata. Audio shorter
    than the frames is padded with silence."""

    total = sum(seconds for _, seconds in frames)
    measured = _loudness(audio)
    loudnorm = (
        f"loudnorm=I={TARGET_LUFS}:TP={TRUE_PEAK_DBTP}:LRA={LOUDNESS_RANGE_LU}"
        f":measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
        f":measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}"
        f":offset={measured['target_offset']}:linear=true"
    )
    audio_chain = f"{loudnorm},aresample={AUDIO_RATE},aformat=channel_layouts=stereo,apad"
    if any(frame.suffix == ".mp4" for frame, _ in frames):
        inputs, labels, chains = [], [], []
        for index, (frame, seconds) in enumerate(frames):
            if frame.suffix == ".mp4":
                inputs += ["-stream_loop", "-1", "-t", f"{seconds:.3f}", "-i", str(frame)]
            else:
                inputs += ["-loop", "1", "-framerate", str(FPS), "-t", f"{seconds:.3f}", "-i", str(frame)]
            chains.append(
                f"[{index}:v]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,"
                f"fps={FPS},format=yuv420p,setsar=1[v{index}]"
            )
            labels.append(f"[v{index}]")
        graph = ";".join(chains) + f";{''.join(labels)}concat=n={len(frames)}:v=1:a=0[v];[{len(frames)}:a]{audio_chain}[a]"
        video_inputs = inputs
    else:
        listing = out.with_suffix(".frames.txt")
        lines = [f"file '{frame.resolve()}'\nduration {seconds:.3f}\n" for frame, seconds in frames]
        # The concat demuxer ignores the last entry's duration unless the file is repeated.
        lines.append(f"file '{frames[-1][0].resolve()}'\n")
        listing.write_text("".join(lines))
        video_inputs = ["-f", "concat", "-safe", "0", "-i", str(listing)]
        graph = f"[0:v]fps={FPS},scale=1920:1080,format=yuv420p[v];[1:a]{audio_chain}[a]"
    run(
        [
            "ffmpeg", "-y", "-v", "error",
            *video_inputs,
            "-i", str(audio),
            "-filter_complex", graph,
            "-map", "[v]", "-map", "[a]", "-t", f"{total:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage", "-g", str(FPS * 2),
            "-c:a", "aac", "-b:a", "160k", "-ar", str(AUDIO_RATE),
            "-metadata", f"title={title}", "-metadata", f"comment={DISCLOSURE}",
            "-movflags", "+faststart",
            str(out),
        ]
    )


def frame_at(*, source: Path, seconds: float, out: Path) -> bytes:
    """One frame of `source` at `seconds`, as a 960-wide JPEG (enough for a
    vision model to read the text, at a quarter of the tokens)."""

    run(
        [
            "ffmpeg", "-y", "-v", "error", "-ss", f"{seconds:.3f}", "-i", str(source),
            "-frames:v", "1", "-vf", "scale=960:-2", "-q:v", "4", str(out),
        ]
    )
    return out.read_bytes()


def extract_speech(*, source: Path, out: Path) -> None:
    """Mono 16 kHz low-bitrate audio for transcription (an hour fits the
    transcription API's 25 MB limit)."""

    run(["ffmpeg", "-y", "-v", "error", "-i", str(source), "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k", str(out)])


@dataclass(frozen=True)
class Measurements:
    width: int
    height: int
    fps: float
    video_codec: str
    audio_codec: str
    audio_rate: int
    duration_seconds: float
    drift_ms: int
    integrated_lufs: float
    true_peak_dbtp: float
    black_seconds: float
    longest_silence_seconds: float

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def _number(pattern: str, text: str, default: float = 0.0) -> float:
    found = re.findall(pattern, text)
    return float(found[-1]) if found else default


def measure(path: Path) -> Measurements:
    """Spec, loudness, black and silence measurements of a finished file."""

    info = probe(path)
    video = next(stream for stream in info["streams"] if stream["codec_type"] == "video")
    audio = next(stream for stream in info["streams"] if stream["codec_type"] == "audio")
    numerator, denominator = (int(part) for part in video["r_frame_rate"].split("/"))
    video_length = float(video.get("duration") or info["format"]["duration"])
    audio_length = float(audio.get("duration") or info["format"]["duration"])

    loudness = run(["ffmpeg", "-v", "info", "-nostats", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"])
    summary = loudness[loudness.rfind("Summary:") :]
    detection = run(
        [
            "ffmpeg", "-v", "info", "-nostats", "-i", str(path),
            "-vf", f"blackdetect=d={BLACK_MIN_SECONDS}:pix_th=0.10",
            "-af", f"silencedetect=noise={SILENCE_THRESHOLD_DB}dB:d={SILENCE_MIN_SECONDS}",
            "-f", "null", "-",
        ]
    )
    black = sum(float(value) for value in re.findall(r"black_duration:\s*([\d.]+)", detection))
    silences = [float(value) for value in re.findall(r"silence_duration:\s*([\d.]+)", detection)]
    return Measurements(
        width=int(video["width"]),
        height=int(video["height"]),
        fps=round(numerator / denominator, 3) if denominator else 0.0,
        video_codec=video["codec_name"],
        audio_codec=audio["codec_name"],
        audio_rate=int(audio["sample_rate"]),
        duration_seconds=round(video_length, 3),
        drift_ms=int(round(abs(video_length - audio_length) * 1000)),
        integrated_lufs=_number(r"I:\s*(-?[\d.]+)\s*LUFS", summary, default=-70.0),
        true_peak_dbtp=_number(r"Peak:\s*(-?[\d.]+)\s*dBFS", summary, default=0.0),
        black_seconds=round(black, 3),
        longest_silence_seconds=round(max(silences, default=0.0), 3),
    )

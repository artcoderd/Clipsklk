import json
import logging
import subprocess
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

from app.config import get_settings
from app.media import PipelineError

log = logging.getLogger(__name__)


def _run(args: list[str], timeout: float | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def probe(path: str | Path) -> dict:
    r = _run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)], 120)
    if r.returncode != 0:
        raise PipelineError("invalid_media", r.stderr[-500:])
    data = json.loads(r.stdout or "{}")
    streams = data.get("streams", [])
    # Cover art in MP3/M4A files shows up as a 1-frame "video" stream; ignore it.
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not s.get("disposition", {}).get("attached_pic")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = float(data.get("format", {}).get("duration") or 0)
    if not audio or duration <= 0:
        raise PipelineError("no_audio" if duration > 0 else "invalid_media")
    fps = None
    if video:
        num, _, den = (video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1").partition("/")
        try:
            fps = round(float(num) / float(den or 1), 3) or None
        except (ValueError, ZeroDivisionError):
            fps = None
    return {
        "duration": duration,
        "fps": fps,
        "has_video": video is not None,
        "width": int(video["width"]) if video else None,
        "height": int(video["height"]) if video else None,
    }


def run_with_progress(args: list[str], duration: float, on_progress: Callable[[float], None] | None) -> None:
    """Runs ffmpeg reporting progress (0..1) from its -progress output."""
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-progress", "pipe:1", "-nostats", *args]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert proc.stdout is not None
    for line in proc.stdout:
        if on_progress and line.startswith("out_time_us=") and duration > 0:
            try:
                on_progress(min(1.0, int(line.split("=")[1]) / 1e6 / duration))
            except ValueError:
                pass
    stderr = proc.stderr.read() if proc.stderr else ""
    if proc.wait() != 0:
        log.error("ffmpeg failed: %s", stderr[-2000:])
        raise PipelineError("ffmpeg_failed", stderr[-300:])


def extract_audio(src: str | Path, dst: str | Path, duration: float,
                  on_progress: Callable[[float], None] | None = None) -> None:
    """16 kHz mono PCM WAV: what Whisper and the audio analysis both consume."""
    run_with_progress(["-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dst)],
                      duration, on_progress)


@lru_cache
def video_encoder() -> str:
    choice = get_settings().video_encoder
    if choice != "auto":
        return choice
    # The encoder may be compiled in while no GPU is present, so try it for real.
    r = _run(["ffmpeg", "-hide_banner", "-v", "error", "-f", "lavfi", "-i", "color=black:s=256x256:d=0.2",
              "-c:v", "h264_nvenc", "-f", "null", "-"], 30)
    enc = "h264_nvenc" if r.returncode == 0 else "libx264"
    log.info("video encoder: %s", enc)
    return enc


def _encoder_args(enc: str) -> list[str]:
    if enc == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "23", "-b:v", "0"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21"]


def frame_image(src: str | Path, at: float, dst: str | Path, vf: str = "scale=640:-2") -> None:
    """One JPEG frame at `at` seconds, through the filter `vf`."""
    r = _run(["ffmpeg", "-hide_banner", "-v", "error", "-y", "-ss", f"{max(0.0, at):.3f}", "-i", str(src),
              "-frames:v", "1", "-vf", vf, "-q:v", "4", str(dst)], 60)
    if r.returncode != 0 or not Path(dst).exists():
        raise PipelineError("ffmpeg_failed", r.stderr[-300:])


def thumbnail(src: str | Path, at: float, dst: str | Path) -> None:
    frame_image(src, at, dst, "scale=540:-2")

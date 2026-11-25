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
    return {
        "duration": duration,
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


# Keep the long side at most 1920 px (phase 3 adds vertical reframing).
_SCALE = "scale='if(gt(iw,ih),min(iw,1920),-2)':'if(gt(iw,ih),-2,min(ih,1920))'"


def cut_clip(src: str | Path, start: float, end: float, dst: str | Path, has_video: bool) -> None:
    dur = max(0.1, end - start)
    if has_video:
        inputs = ["-ss", f"{start:.3f}", "-i", str(src), "-t", f"{dur:.3f}"]
        maps = ["-map", "0:v:0", "-map", "0:a:0", "-vf", f"{_SCALE},format=yuv420p"]
    else:
        # Audio-only source (podcast): black frame until phase 3 adds visuals.
        inputs = ["-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src),
                  "-f", "lavfi", "-t", f"{dur:.3f}", "-i", "color=c=black:s=1280x720:r=30"]
        maps = ["-map", "1:v:0", "-map", "0:a:0", "-pix_fmt", "yuv420p", "-shortest"]
    args = [*inputs, *maps, *_encoder_args(video_encoder()),
            "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart", str(dst)]
    run_with_progress(args, dur, None)


def thumbnail(src: str | Path, at: float, dst: str | Path) -> None:
    r = _run(["ffmpeg", "-hide_banner", "-v", "error", "-y", "-ss", f"{max(0.0, at):.3f}", "-i", str(src),
              "-frames:v", "1", "-vf", "scale=640:-2", "-q:v", "4", str(dst)], 60)
    if r.returncode != 0:
        raise PipelineError("ffmpeg_failed", r.stderr[-300:])

"""Downloads a video from a supported platform with yt-dlp."""
import logging
from collections.abc import Callable
from pathlib import Path

from app.config import get_settings
from app.media import PipelineError

log = logging.getLogger(__name__)

# Fragments of yt-dlp error messages -> stable codes the web app explains.
_ERRORS = [
    ("sign in to confirm", "source_blocked"),   # YouTube bot check on server IPs
    ("confirm you're not a bot", "source_blocked"),
    ("http error 403", "source_blocked"),
    ("http error 429", "source_blocked"),
    ("private video", "source_private"),
    ("this video is private", "source_private"),
    ("login required", "source_private"),
    ("requires authentication", "source_private"),
    ("members-only", "source_private"),
    ("confirm your age", "source_private"),
    ("age-restricted", "source_private"),
    ("video unavailable", "source_unavailable"),
    ("has been removed", "source_unavailable"),
    ("does not exist", "source_unavailable"),
    ("not available", "source_unavailable"),
    ("unsupported url", "unsupported_url"),
]


def _map_error(msg: str) -> str:
    low = msg.lower()
    return next((code for frag, code in _ERRORS if frag in low), "download_failed")


def _base_opts() -> dict:
    s = get_settings()
    opts = {
        "quiet": True,
        "noprogress": True,  # we report progress ourselves via progress_hooks
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 5,
        "fragment_retries": 10,
        "concurrent_fragment_downloads": 4,
    }
    if s.ytdlp_cookies_file:
        opts["cookiefile"] = s.ytdlp_cookies_file
    return opts


def download(url: str, dest_dir: Path, on_progress: Callable[[float], None] | None = None,
             max_bytes: int | None = None) -> tuple[Path, dict]:
    """Returns (file path, info). Refuses live streams (that's live mode)."""
    import yt_dlp

    s = get_settings()
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        with yt_dlp.YoutubeDL(_base_opts()) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        raise PipelineError(_map_error(str(e)), str(e)[-300:]) from e
    if info.get("_type") == "playlist":
        raise PipelineError("unsupported_url", "playlist")
    if info.get("is_live"):
        raise PipelineError("source_is_live")
    duration = info.get("duration") or 0
    if duration and duration > s.max_source_hours * 3600:
        raise PipelineError("source_too_long")

    progress: dict[str, tuple[float, float]] = {}

    def hook(d: dict) -> None:
        if not on_progress or d.get("status") not in ("downloading", "finished"):
            return
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        done = d.get("downloaded_bytes") or 0
        if d.get("status") == "finished":
            done = total = max(done, total, 1)
        progress[d.get("filename", "")] = (done, total)
        got = sum(p[0] for p in progress.values())
        want = sum(p[1] for p in progress.values())
        if want:
            # Video + audio arrive as two files; the audio is small, so this is close enough.
            on_progress(min(0.99, got / want))

    opts = {
        **_base_opts(),
        # Best quality up to 1080p; merged into an mp4/mkv by ffmpeg.
        "format": "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b",
        "merge_output_format": "mp4",
        "outtmpl": str(dest_dir / "source.%(ext)s"),
        "progress_hooks": [hook],
    }
    if max_bytes:
        opts["max_filesize"] = max_bytes
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.process_ie_result(info, download=True)
    except yt_dlp.utils.DownloadError as e:
        raise PipelineError(_map_error(str(e)), str(e)[-300:]) from e

    files = [p for p in dest_dir.glob("source.*") if not p.name.endswith((".part", ".ytdl"))]
    if not files:
        raise PipelineError("download_failed", "no output file")
    if on_progress:
        on_progress(1.0)
    return max(files, key=lambda p: p.stat().st_size), {"title": info.get("title"), "duration": duration}

"""Records a live stream as 10-second MPEG-TS segments, without re-encoding.

    streamlink (or yt-dlp) --stdout  ->  ffmpeg -f segment -c copy

The timeline is the recording itself: segment N starts where N-1 ended, so
gaps while the stream was down are skipped. Every segment also keeps the
wall-clock time it covers, which is how chat messages are placed on the
timeline. If the stream drops, it reconnects with back-off and gives up only
after `offline_grace` seconds without new video.
"""
import csv
import json
import logging
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger(__name__)

SEGMENT_SECONDS = 10
QUALITY = "1080p60,1080p,720p60,720p,best"


@dataclass
class Segment:
    index: int
    file: str
    duration: float
    start: float          # position in the recording (s)
    wall_start: float     # unix time this segment began (approx.)


class Timeline:
    """Thread-safe list of finished segments, persisted as manifest.json."""

    def __init__(self, root: Path):
        self.root = root
        self.segments: list[Segment] = []
        self.lock = threading.Lock()

    @property
    def duration(self) -> float:
        with self.lock:
            return self.segments[-1].start + self.segments[-1].duration if self.segments else 0.0

    def add(self, file: str, duration: float, wall_end: float) -> Segment:
        with self.lock:
            start = self.segments[-1].start + self.segments[-1].duration if self.segments else 0.0
            seg = Segment(len(self.segments), file, round(duration, 3), round(start, 3),
                          round(wall_end - duration, 3))
            self.segments.append(seg)
            tmp = self.root / "manifest.json.tmp"
            tmp.write_text(json.dumps([asdict(s) for s in self.segments]))
            tmp.replace(self.root / "manifest.json")
            return seg

    def wall_to_t(self, wall: float) -> float | None:
        """Recording position of a wall-clock instant (None if it was in a gap)."""
        with self.lock:
            for s in reversed(self.segments):
                if s.wall_start <= wall <= s.wall_start + s.duration + 1.0:
                    return s.start + min(s.duration, max(0.0, wall - s.wall_start))
                if s.wall_start + s.duration < wall:
                    return None
        return None

    def covering(self, t0: float, t1: float) -> list[Segment]:
        with self.lock:
            return [s for s in self.segments if s.start < t1 and s.start + s.duration > t0]

    @staticmethod
    def load(root: Path) -> "Timeline":
        tl = Timeline(root)
        mf = root / "manifest.json"
        if mf.exists():
            tl.segments = [Segment(**d) for d in json.loads(mf.read_text())]
        return tl


def source_command(url: str, use_ytdlp: bool = False) -> list[str]:
    if use_ytdlp:
        return ["yt-dlp", "--quiet", "--no-warnings", "-f", "best", "-o", "-", url]
    return ["streamlink", "--stdout", "--loglevel", "error", "--retry-open", "2",
            "--stream-timeout", "30", "--hls-live-edge", "3", url, QUALITY]


def streamlink_can_handle(url: str) -> bool:
    r = subprocess.run(["streamlink", "--can-handle-url", url], capture_output=True, timeout=30)
    return r.returncode == 0


class Recorder:
    def __init__(self, url: str, root: Path, timeline: Timeline, stop: threading.Event,
                 on_segment: Callable[[Segment], None] | None = None,
                 on_status: Callable[[str], None] | None = None,
                 offline_grace: float = 600, max_seconds: float = 12 * 3600):
        self.url, self.root, self.timeline, self.stop = url, root, timeline, stop
        self.on_segment = on_segment or (lambda s: None)
        self.on_status = on_status or (lambda s: None)
        self.offline_grace, self.max_seconds = offline_grace, max_seconds
        self.session = 0
        self.status = ""

    def _set_status(self, status: str) -> None:
        if status != self.status:
            self.status = status
            self.on_status(status)

    def run(self) -> str:
        """Blocks until the broadcast ends, is stopped or hits the time limit.
        Returns why: "stopped" | "ended" | "max_duration"."""
        self.root.mkdir(parents=True, exist_ok=True)
        use_ytdlp = not streamlink_can_handle(self.url) and shutil.which("yt-dlp") is not None
        last_video = time.time()
        backoff = 5.0
        while not self.stop.is_set():
            if self.timeline.duration >= self.max_seconds:
                return "max_duration"
            if not self.status:
                self._set_status("waiting")
            got = self._session(use_ytdlp)
            if self.stop.is_set():
                return "stopped"
            if got:
                last_video, backoff = time.time(), 5.0
            if time.time() - last_video > self.offline_grace:
                return "ended"
            # The stream dropped or hasn't started: try again, a bit later each time.
            self._set_status("reconnecting" if self.timeline.segments else "waiting")
            self.stop.wait(backoff)
            backoff = min(60.0, backoff * 1.6)
        return "stopped"

    def _session(self, use_ytdlp: bool) -> bool:
        """One connection to the stream. Returns True if any video arrived."""
        self.session += 1
        start_n = len(self.timeline.segments)
        seglist = self.root / f"list_{self.session}.csv"
        src = subprocess.Popen(source_command(self.url, use_ytdlp), stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        enc = subprocess.Popen(
            ["ffmpeg", "-hide_banner", "-v", "error", "-nostdin", "-fflags", "+genpts", "-i", "pipe:0",
             "-map", "0:v:0?", "-map", "0:a:0?", "-c", "copy", "-f", "segment",
             "-segment_time", str(SEGMENT_SECONDS), "-segment_format", "mpegts", "-reset_timestamps", "1",
             "-segment_list", str(seglist), "-segment_list_type", "csv",
             "-segment_start_number", str(start_n), str(self.root / f"s{self.session}_%06d.ts")],
            stdin=src.stdout, stderr=subprocess.DEVNULL)
        src.stdout.close()   # ffmpeg owns the pipe now
        seen = 0
        try:
            while True:
                seen = self._collect(seglist, seen)
                if enc.poll() is not None:
                    break
                if self.stop.wait(1.0):
                    break
        finally:
            for p in (src, enc):
                if p.poll() is None:
                    p.terminate()
            for p in (src, enc):
                try:
                    p.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    p.kill()
            self._collect(seglist, seen)   # last (partial) segment
        return len(self.timeline.segments) > start_n

    def _collect(self, seglist: Path, seen: int) -> int:
        if not seglist.exists():
            return seen
        with open(seglist, newline="") as f:
            rows = list(csv.reader(f))
        for row in rows[seen:]:
            if len(row) < 3:
                continue
            name, a, b = row[0], float(row[1]), float(row[2])
            path = self.root / name
            if b - a < 0.2 or not path.exists():
                continue
            seg = self.timeline.add(name, b - a, time.time())
            self._set_status("recording")   # only once video actually flows
            try:
                self.on_segment(seg)
            except Exception:
                log.exception("segment callback failed")
        return len(rows)

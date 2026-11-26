"""Runs one live session (Celery task on the "live" queue):

    recorder thread  -> 10 s segments + timeline
    chat thread      -> messages per wall-clock second
    main loop        -> audio of each new segment -> LiveDetector -> auto clips
                        commands from the web (clip now, stop, settings)
                        status for the web every 2 s, heartbeat
    at the end       -> whole recording -> normal pipeline for the best clips

Clip windows are cut from the segments immediately (stream copy, cheap) and
stored as their own source, so the GPU worker can render them whenever it
gets to them, even after the segments are gone.
"""
import json
import logging
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from app import events
from app.config import get_settings
from app.db import SessionLocal
from app.live.chat import ChatMeter, make_chat
from app.live_keys import command_key, live_channel
from app.live.detector import AutoClipper, LiveDetector, Peak
from app.live.recorder import Recorder, Segment, Timeline
from app.models import Clip, Job, JobSource, JobStatus, LiveSession, MediaAsset
from app.redis_client import get_redis
from app.schemas import ClipOut, JobOut, LiveOut
from app.storage import get_storage

log = logging.getLogger(__name__)


def publish(session_id, kind: str, payload: dict) -> None:
    get_redis().publish(live_channel(session_id), json.dumps({"type": kind, kind: payload}, default=str))


def segment_samples(path: Path) -> np.ndarray:
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000",
                        "-f", "s16le", "-"], capture_output=True, timeout=120)
    return np.frombuffer(r.stdout, np.int16).astype(np.float32) / 32768.0


def cut_window(timeline: Timeline, rec_dir: Path, t0: float, t1: float, dest: Path) -> float:
    """Joins the segments covering [t0, t1] (no re-encode). Returns the
    recording time at which `dest` starts."""
    segs = timeline.covering(t0, t1)
    if not segs:
        raise RuntimeError("no segments for window")
    lst = dest.with_suffix(".txt")
    lst.write_text("".join(f"file '{rec_dir / s.file}'\n" for s in segs))
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                        "-c", "copy", "-bsf:a", "aac_adtstoasc", "-movflags", "+faststart", str(dest)],
                       capture_output=True, text=True, timeout=600)
    lst.unlink(missing_ok=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-300:])
    return segs[0].start


class LiveController:
    def __init__(self, session_id: str):
        self.sid = session_id
        s = get_settings()
        self.root = Path(s.live_dir) / session_id
        self.rec_dir = self.root / "rec"
        self.stop = threading.Event()
        self.timeline = Timeline(self.rec_dir)
        self.meter = ChatMeter()
        self.chat_status = "none"
        self.status = "starting"
        self.pending_segments: list[Segment] = []
        self.pending_clips: list[tuple[float, float, list[str], float]] = []   # (start, end, reasons, score)
        self.seg_lock = threading.Lock()

    # -- helpers --------------------------------------------------------------
    def _db_update(self, **fields) -> LiveSession:
        db = SessionLocal()
        try:
            live = db.get(LiveSession, uuid.UUID(self.sid))
            for k, v in fields.items():
                setattr(live, k, v)
            db.commit()
            publish(self.sid, "live", LiveOut.model_validate(live).model_dump(mode="json"))
            return live
        finally:
            db.close()

    def _chat_at(self, t: int) -> tuple[int, int, bool]:
        """Chat activity during recording second t."""
        wall = self._t_to_wall(t + 0.5)
        if wall is None:
            return 0, 0, False
        m, h = self.meter.get(int(wall))
        return m, h, self.chat_status == "connected" or self.meter.total > 0

    def _t_to_wall(self, t: float) -> float | None:
        with self.timeline.lock:
            for s in reversed(self.timeline.segments):
                if s.start <= t <= s.start + s.duration:
                    return s.wall_start + (t - s.start)
        return None

    # -- main ------------------------------------------------------------------
    def run(self) -> None:
        s = get_settings()
        db = SessionLocal()
        live = db.get(LiveSession, uuid.UUID(self.sid))
        url, platform, user_id, job_id = live.url, live.platform, live.user_id, live.job_id
        clipper = AutoClipper(clip_seconds=live.clip_duration, sensitivity=live.sensitivity,
                              max_per_hour=s.live_max_auto_clips_per_hour)
        self.auto = live.auto_clips
        self._ai_review = live.ai_review
        db.close()
        self.rec_dir.mkdir(parents=True, exist_ok=True)
        detector = LiveDetector(self._chat_at, clipper)

        def on_status(st: str):
            self.status = st
            self._db_update(status=st)

        def on_chat_status(st: str):
            self.chat_status = st
            self._db_update(chat_status=st)

        chat = make_chat(platform, url, self.meter.add, on_chat_status, self.stop)
        if chat:
            threading.Thread(target=chat.run, name="chat", daemon=True).start()
        recorder = Recorder(url, self.rec_dir, self.timeline, self.stop,
                            on_segment=lambda seg: self._queue_segment(seg), on_status=on_status,
                            offline_grace=s.live_offline_grace_minutes * 60, max_seconds=s.live_max_hours * 3600)
        result: dict = {}
        rec_thread = threading.Thread(target=lambda: result.setdefault("reason", recorder.run()), name="recorder")
        rec_thread.start()

        from app.ai.providers import configured as ai_configured
        review_every = s.live_ai_review_minutes * 60
        ai_review = bool(self._ai_review and ai_configured() and review_every > 0)
        last_review = 0.0
        charged_min, out_of_minutes = 0, False
        clip_now: list[tuple[float, int]] = []   # (wall time of the tap, seconds)
        last_status = last_beat = 0.0
        try:
            while rec_thread.is_alive() or self.pending_segments:
                # 1) commands from the web
                for cmd in self._commands():
                    if cmd["type"] == "stop":
                        self.stop.set()
                    elif cmd["type"] == "clip_now":
                        clip_now.append((cmd["wall"], int(cmd["seconds"])))
                    elif cmd["type"] == "settings":
                        if "auto_clips" in cmd:
                            self.auto = bool(cmd["auto_clips"])
                        if cmd.get("sensitivity"):
                            clipper.sensitivity = cmd["sensitivity"]
                # 2) analyse new audio
                with self.seg_lock:
                    segs, self.pending_segments = self.pending_segments, []
                for seg in segs:
                    for peak in detector.feed(segment_samples(self.rec_dir / seg.file)):
                        if self.auto:
                            a, b = clipper.window(peak)
                            self.pending_clips.append((a, b, peak.reasons, peak.score))
                # 3) "¡Clip ahora!": the last N seconds before the tap
                for item in list(clip_now):
                    wall, secs = item
                    t = self.timeline.wall_to_t(wall)
                    newest = self.timeline.segments[-1] if self.timeline.segments else None
                    if t is None and newest and newest.wall_start + newest.duration >= wall:
                        t = self.timeline.duration   # tap landed in a gap: use what we have
                    if t is not None:
                        self.pending_clips.append((max(0.0, t - secs), t + 2.0, ["clip ahora"], 1.0))
                        clip_now.remove(item)
                    elif not rec_thread.is_alive():
                        clip_now.remove(item)
                # 4) cut clips whose window is fully recorded
                for item in list(self.pending_clips):
                    if self.timeline.duration >= item[1] or not rec_thread.is_alive():
                        self.pending_clips.remove(item)
                        self._make_clip(user_id, job_id, *item)
                # 5) every few minutes the AI reads the last stretch and may propose a clip
                if ai_review and self.auto and self.timeline.duration - last_review >= review_every:
                    end_t = self.timeline.duration
                    self._ai_review_window(user_id, max(0.0, last_review - 15), end_t)
                    last_review = end_t
                # 6) minutes: each recorded minute is charged as it happens; stop when they run out
                while self.timeline.duration >= (charged_min + 1) * 60:
                    if not self._charge_minute(user_id, charged_min):
                        self.stop.set()
                        out_of_minutes = True
                        break
                    charged_min += 1
                # 7) status for the web + heartbeat
                now = time.time()
                if now - last_status >= 2:
                    last_status = now
                    publish(self.sid, "activity", self._activity(detector))
                if now - last_beat >= 10:
                    last_beat = now
                    self._db_update(recorded_seconds=round(self.timeline.duration, 1),
                                    heartbeat_at=datetime.now(timezone.utc))
                if not segs:
                    time.sleep(0.5)
        finally:
            self.stop.set()
            rec_thread.join(timeout=60)
        self._finish("no_minutes" if out_of_minutes else result.get("reason", "stopped"), user_id)

    def _queue_segment(self, seg: Segment) -> None:
        with self.seg_lock:
            self.pending_segments.append(seg)

    def _commands(self) -> list[dict]:
        r = get_redis()
        out = []
        while (raw := r.lpop(command_key(self.sid))) is not None:
            try:
                out.append(json.loads(raw))
            except ValueError:
                pass
        return out

    def _activity(self, detector: LiveDetector) -> dict:
        p = detector.last
        if not p:
            return {"recorded": round(self.timeline.duration, 1), "interest": [], "chat": []}
        return {"recorded": round(self.timeline.duration, 1),
                "interest": [round(float(v), 2) for v in p["interest"][-120:]],
                "chat": [int(v) for v in p["rate"][-120:]],
                "chat_messages": self.meter.total}

    def _make_clip(self, user_id, job_id, start: float, end: float, reasons: list[str], score: float) -> None:
        """Cuts the window now and hands rendering to the video workers."""
        from app.worker import enqueue_live_clip

        end = min(end, self.timeline.duration)
        if end - start < 3:
            return
        db = SessionLocal()
        try:
            clip_id = uuid.uuid4()
            work = self.root / "clips"
            work.mkdir(exist_ok=True)
            dest = work / f"{clip_id}.mp4"
            offset = cut_window(self.timeline, self.rec_dir, start - 3, end + 3, dest)
            key = f"users/{user_id}/live/{self.sid}/clips/{clip_id}.mp4"
            get_storage().put_file(key, dest, "video/mp4")
            dest.unlink(missing_ok=True)
            asset = MediaAsset(user_id=user_id, source_key=f"live:{self.sid}:{clip_id}", storage_key=key,
                               title=None)
            db.add(asset)
            db.flush()
            index = (db.query(Clip).filter(Clip.job_id == job_id).count()) + 1
            job = db.get(Job, job_id)
            clip = Clip(id=clip_id, job_id=job_id, user_id=user_id, index=index, asset_id=asset.id, offset=offset,
                        start=round(start - offset, 2), end=round(end - offset, 2), score=round(score, 3),
                        reasons=reasons, render=dict(job.render or {}), render_status="rendering", version=0)
            db.add(clip)
            job.clip_count = index
            db.commit()
            publish(self.sid, "clip", ClipOut.model_validate(clip).model_dump(mode="json"))
            enqueue_live_clip(clip.id)
        except Exception:
            log.exception("live clip failed")
            db.rollback()
        finally:
            db.close()

    def _charge_minute(self, user_id, index: int) -> bool:
        from app.billing import NoMinutes, charge
        from app.models import User

        db = SessionLocal()
        try:
            charge(db, db.get(User, user_id), 1, f"live:{self.sid}:{index}", "Minuto de directo")
            db.commit()
            return True
        except NoMinutes:
            db.rollback()
            return False
        finally:
            db.close()

    def _ai_review_window(self, user_id, t0: float, t1: float) -> None:
        """Cuts [t0, t1] into its own source and asks the GPU worker's AI review to look at it."""
        from app.worker import enqueue_live_review

        db = SessionLocal()
        try:
            asset_id = uuid.uuid4()
            dest = self.root / f"review-{asset_id}.mp4"
            offset = cut_window(self.timeline, self.rec_dir, t0, t1, dest)
            key = f"users/{user_id}/live/{self.sid}/review/{asset_id}.mp4"
            get_storage().put_file(key, dest, "video/mp4")
            dest.unlink(missing_ok=True)
            db.add(MediaAsset(id=asset_id, user_id=user_id, source_key=f"live:{self.sid}:review:{asset_id}",
                              storage_key=key))
            db.commit()
            enqueue_live_review(self.sid, asset_id, offset)
        except Exception:
            log.exception("AI review window failed")
            db.rollback()
        finally:
            db.close()

    def _finish(self, reason: str, user_id) -> None:
        """Ends the session; the whole recording goes through the normal
        pipeline to pick the best clips of the broadcast."""
        from app.worker import enqueue_job

        dur = self.timeline.duration
        live = self._db_update(status="finishing", end_reason=reason, recorded_seconds=round(dur, 1),
                               chat_status="none")
        final_job_id = None
        try:
            if live.final_clips > 0 and dur >= 30:
                rec = self.root / "recording.mp4"
                cut_window(self.timeline, self.rec_dir, 0, dur + 1, rec)
                key = f"users/{user_id}/live/{self.sid}/recording.mp4"
                get_storage().put_file(key, rec, "video/mp4")
                db = SessionLocal()
                try:
                    job = Job(user_id=user_id, source=JobSource.live, original_filename="Directo completo", prepaid=True,
                              title="Directo · lo mejor", storage_key=key, clip_count=live.final_clips,
                              clip_duration=live.clip_duration, render=dict(live.render or {}))
                    db.add(job)
                    db.commit()
                    final_job_id = job.id
                finally:
                    db.close()
                enqueue_job(final_job_id)
            status = "ended" if dur > 0 else "failed"
            self._db_update(status=status, final_job_id=final_job_id, ended_at=datetime.now(timezone.utc),
                            error=None if dur > 0 else "stream_unavailable")
        except Exception:
            log.exception("finishing live %s failed", self.sid)
            self._db_update(status="failed", error="finish_failed", ended_at=datetime.now(timezone.utc))
        finally:
            shutil.rmtree(self.root, ignore_errors=True)
            # The live clips' job is complete too.
            db = SessionLocal()
            try:
                live = db.get(LiveSession, uuid.UUID(self.sid))
                job = db.get(Job, live.job_id)
                job.status, job.stage, job.progress = JobStatus.done, "done", 100
                db.commit()
                events.publish(job.id, "job", JobOut.model_validate(job).model_dump(mode="json"))
            finally:
                db.close()

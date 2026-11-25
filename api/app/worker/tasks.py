"""The recorded-video pipeline.

    fetch (yt-dlp or upload) -> probe -> audio -> transcribe -> analyze -> select -> cut

The source, transcript and analysis are stored on the MediaAsset, so asking
for more clips (or pasting the same link again) skips straight to select+cut.
"""
import gzip
import hashlib
import json
import logging
import shutil
import time
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app import events
from app.config import get_settings
from app.db import SessionLocal
from app.media import PipelineError, ffmpeg
from app.media.analysis import analyze
from app.media.download import download
from app.media.moments import select_moments
from app.media.transcribe import transcribe
from app.models import Clip, Job, JobSource, JobStatus, MediaAsset
from app.platforms import canonical_source
from app.redis_client import get_redis
from app.schemas import ClipOut, JobOut
from app.storage import get_storage
from app.worker.celery_app import celery_app

log = logging.getLogger(__name__)

# Progress ranges (start %, end %) for each stage shown to the user.
STAGES = {
    "fetching": (2, 25),
    "extracting_audio": (25, 30),
    "transcribing": (30, 72),
    "detecting_moments": (72, 78),
    "cutting": (80, 99),
}


class Progress:
    """Writes progress to the DB and Redis, throttled so long stages don't spam."""

    def __init__(self, db, job: Job):
        self.db, self.job, self._last = db, job, 0.0

    def push(self, **fields) -> None:
        for k, v in fields.items():
            setattr(self.job, k, v)
        self.db.commit()
        events.publish(self.job.id, "job", JobOut.model_validate(self.job).model_dump(mode="json"))
        self._last = time.monotonic()

    def stage(self, name: str) -> None:
        self.push(stage=name, progress=max(self.job.progress, STAGES[name][0]))

    def within(self, name: str):
        lo, hi = STAGES[name]

        def cb(frac: float) -> None:
            pct = int(lo + (hi - lo) * max(0.0, min(1.0, frac)))
            if pct > self.job.progress and time.monotonic() - self._last >= 0.5:
                self.push(progress=pct)
        return cb


def _save_json(key: str, data: dict) -> None:
    tmp = Path(get_settings().work_dir) / f"{uuid.uuid4()}.json.gz"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    try:
        get_storage().put_file(key, tmp, "application/gzip")
    finally:
        tmp.unlink(missing_ok=True)


def _load_json(key: str, work: Path) -> dict:
    tmp = work / Path(key).name
    get_storage().get_file(key, tmp)
    with gzip.open(tmp, "rt", encoding="utf-8") as f:
        return json.load(f)


def _get_asset(db, job: Job) -> MediaAsset:
    if job.asset_id:
        return db.get(MediaAsset, job.asset_id)
    key = canonical_source(job.source_url) if job.source == JobSource.url else f"upload:{job.id}"
    asset = db.scalar(select(MediaAsset).where(MediaAsset.user_id == job.user_id, MediaAsset.source_key == key))
    if asset is None:
        asset = MediaAsset(user_id=job.user_id, source_key=key)
        if job.source == JobSource.upload:
            asset.storage_key = job.storage_key
            asset.title = job.original_filename
        db.add(asset)
        try:
            db.commit()
        except IntegrityError:  # created concurrently by another job
            db.rollback()
            asset = db.scalar(select(MediaAsset).where(MediaAsset.user_id == job.user_id,
                                                       MediaAsset.source_key == key))
    job.asset_id = asset.id
    db.commit()
    return asset


def _source_file(asset: MediaAsset, work: Path) -> Path:
    """A local path to the source (direct on local storage, downloaded from S3/R2)."""
    storage = get_storage()
    local = storage.local_path(asset.storage_key)
    if local is not None:
        return local
    dest = work / ("source" + Path(asset.storage_key).suffix)
    if not dest.exists():
        storage.get_file(asset.storage_key, dest)
    return dest


def _prepare_asset(db, job: Job, asset: MediaAsset, prog: Progress, work: Path) -> None:
    s = get_settings()
    storage = get_storage()
    base = f"users/{job.user_id}/assets/{asset.id}"

    if not asset.storage_key:
        prog.stage("fetching")
        path, info = download(job.source_url, work / "dl", prog.within("fetching"),
                              max_bytes=s.max_upload_mb * 1024 * 1024)
        key = f"{base}/source{path.suffix}"
        storage.put_file(key, path)
        asset.storage_key, asset.title = key, (info.get("title") or "")[:300] or None
        db.commit()
        # Keep the download as the local working copy to avoid fetching it back.
        if storage.local_path(key) is None:
            path.rename(work / ("source" + path.suffix))

    src = None
    if asset.duration is None:
        src = _source_file(asset, work)
        meta = ffmpeg.probe(src)
        if meta["duration"] > s.max_source_hours * 3600:
            raise PipelineError("source_too_long")
        asset.duration, asset.width, asset.height, asset.has_video = (
            meta["duration"], meta["width"], meta["height"], meta["has_video"])
        db.commit()

    if asset.transcript_key and asset.analysis_key:
        return

    src = src or _source_file(asset, work)
    wav = work / "audio.wav"
    prog.stage("extracting_audio")
    ffmpeg.extract_audio(src, wav, asset.duration, prog.within("extracting_audio"))

    if not asset.transcript_key:
        prog.stage("transcribing")
        tr = transcribe(wav, asset.duration, prog.within("transcribing"))
        _save_json(f"{base}/transcript.json.gz", tr)
        asset.transcript_key, asset.language = f"{base}/transcript.json.gz", tr.get("language")
        db.commit()

    if not asset.analysis_key:
        prog.stage("detecting_moments")
        _save_json(f"{base}/analysis.json.gz", analyze(wav))
        asset.analysis_key = f"{base}/analysis.json.gz"
        db.commit()


def _make_clips(db, job: Job, asset: MediaAsset, prog: Progress, work: Path) -> None:
    storage = get_storage()
    existing = list(db.scalars(select(Clip).where(Clip.job_id == job.id)))
    missing = job.clip_count - len(existing)
    if missing <= 0:
        return
    prog.stage("detecting_moments")
    transcript = _load_json(asset.transcript_key, work)
    analysis = _load_json(asset.analysis_key, work)
    moments = select_moments(analysis, transcript, missing, job.clip_duration, asset.duration,
                             exclude=[(c.start, c.end) for c in existing])
    if not moments and not existing:
        raise PipelineError("no_moments")

    prog.stage("cutting")
    src = _source_file(asset, work)
    cut_progress = prog.within("cutting")
    next_index = max((c.index for c in existing), default=0) + 1
    for i, m in enumerate(moments):
        clip = Clip(job_id=job.id, user_id=job.user_id, index=next_index + i, start=m.start, end=m.end,
                    score=m.score, reasons=m.reasons, text=m.text[:5000])
        db.add(clip)
        db.flush()
        base = f"users/{job.user_id}/clips/{clip.id}"
        out, thumb = work / f"{clip.id}.mp4", work / f"{clip.id}.jpg"
        ffmpeg.cut_clip(src, m.start, m.end, out, asset.has_video)
        ffmpeg.thumbnail(out, min((m.end - m.start) * 0.6, m.end - m.start - 0.1), thumb)
        storage.put_file(f"{base}.mp4", out, "video/mp4")
        storage.put_file(f"{base}.jpg", thumb, "image/jpeg")
        out.unlink(missing_ok=True)
        thumb.unlink(missing_ok=True)
        clip.storage_key, clip.thumbnail_key = f"{base}.mp4", f"{base}.jpg"
        db.commit()
        events.publish(job.id, "clip", ClipOut.model_validate(clip).model_dump(mode="json"))
        cut_progress((i + 1) / len(moments))
    # Fewer good windows than requested (e.g. short video): report what we have.
    if len(existing) + len(moments) < job.clip_count:
        job.clip_count = len(existing) + len(moments)


def _asset_lock(job: Job):
    key = canonical_source(job.source_url) if job.source == JobSource.url else f"upload:{job.id}"
    digest = hashlib.sha256(f"{job.user_id}:{key}".encode()).hexdigest()[:32]
    # Two jobs for the same video wait for each other instead of downloading twice.
    return get_redis().lock(f"lock:asset:{digest}", timeout=6 * 3600, blocking_timeout=6 * 3600)


@celery_app.task(name="app.worker.tasks.process_job", bind=True)
def process_job(self, job_id: str) -> None:
    db = SessionLocal()
    work = Path(get_settings().work_dir) / job_id
    job = None
    try:
        job = db.get(Job, uuid.UUID(job_id))
        if job is None or job.status not in (JobStatus.queued, JobStatus.running):
            return
        prog = Progress(db, job)
        prog.push(status=JobStatus.running, stage="starting", progress=max(job.progress, 1), error=None)
        work.mkdir(parents=True, exist_ok=True)
        with _asset_lock(job):
            asset = _get_asset(db, job)
            _prepare_asset(db, job, asset, prog, work)
        if asset.title and not job.title:
            job.title = asset.title
        _make_clips(db, job, asset, prog, work)
        prog.push(status=JobStatus.done, stage="done", progress=100)
    except PipelineError as e:
        log.warning("job %s failed: %s", job_id, e)
        _fail(db, job, e.code)
    except Exception:
        log.exception("job %s crashed", job_id)
        _fail(db, job, "processing_failed")
    finally:
        shutil.rmtree(work, ignore_errors=True)
        db.close()


def _fail(db, job: Job | None, code: str) -> None:
    if job is None:
        return
    db.rollback()
    Progress(db, job).push(status=JobStatus.failed, stage="failed", error=code)

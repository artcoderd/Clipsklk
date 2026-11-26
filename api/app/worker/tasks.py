"""The recorded-video pipeline.

    fetch (yt-dlp or upload) -> probe -> audio -> transcribe -> analyze audio
    -> faces/people -> select moments -> render vertical clips

Everything up to "faces" is stored on the MediaAsset, so more clips, clips of
one person, or re-rendering a clip with other options never re-download or
re-transcribe.
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
from app.media.render import RenderSettings, render_clip
from app.media.subtitles import PRESETS
from app.media.transcribe import transcribe
from app.media.vision import analyze_video, person_speaking_seconds
from app.models import Clip, Job, JobSource, JobStatus, LibraryItem, MediaAsset, SubtitleStyle
from app.platforms import canonical_source
from app.redis_client import get_redis
from app.schemas import ClipOut, JobOut, RenderOptions
from app.storage import get_storage
from app.worker.celery_app import celery_app

log = logging.getLogger(__name__)

# Progress ranges (start %, end %) for each stage shown to the user.
STAGES = {
    "fetching": (2, 20),
    "extracting_audio": (20, 24),
    "transcribing": (24, 56),
    "detecting_moments": (56, 60),
    "detecting_faces": (60, 76),
    "rendering": (78, 99),
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


def _load_json(key: str | None, work: Path) -> dict | None:
    if not key:
        return None
    tmp = work / Path(key).name
    get_storage().get_file(key, tmp)
    with gzip.open(tmp, "rt", encoding="utf-8") as f:
        return json.load(f)


def _asset_key(job: Job) -> str:
    return canonical_source(job.source_url) if job.source == JobSource.url else f"upload:{job.id}"


def _get_asset(db, job: Job) -> MediaAsset:
    if job.asset_id:
        return db.get(MediaAsset, job.asset_id)
    key = _asset_key(job)
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


def _person_thumb(src: Path, person: dict, dest: Path, duration: float) -> None:
    """Square crop around the person's face at their best-framed moment."""
    x, y, w, h = person["thumb_box"]
    # thumb_box is normalised; a square of ~1.8 face widths, clamped to the frame.
    cx, cy = x + w / 2, y + h / 2
    side = min(1.0, max(w, h) * 1.8)
    vf = (f"crop=w='min(iw,ih)*{side:.4f}':h='min(iw,ih)*{side:.4f}':"
          f"x='max(0,min(iw-ow,iw*{cx:.4f}-ow/2))':y='max(0,min(ih-oh,ih*{cy:.4f}-oh/2))',scale=240:240")
    ffmpeg.frame_image(src, min(person["thumb_t"], max(0.0, duration - 0.1)), dest, vf)


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
        asset.duration, asset.width, asset.height, asset.has_video, asset.fps = (
            meta["duration"], meta["width"], meta["height"], meta["has_video"], meta["fps"])
        db.commit()

    if not (asset.transcript_key and asset.analysis_key):
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
        wav.unlink(missing_ok=True)

    if asset.has_video and not asset.vision_key:
        src = src or _source_file(asset, work)
        prog.stage("detecting_faces")
        vision = analyze_video(src, asset.width, asset.height, asset.duration, prog.within("detecting_faces"),
                               hwaccel=ffmpeg.video_encoder() == "h264_nvenc")
        people = []
        for p in vision["people"][:8]:   # thumbnails for the "choose a person" picker
            key = f"{base}/people/{p['id']}.jpg"
            try:
                _person_thumb(src, p, work / f"person{p['id']}.jpg", asset.duration)
                storage.put_file(key, work / f"person{p['id']}.jpg", "image/jpeg")
            except PipelineError:
                key = None
            people.append({"id": p["id"], "seconds": p["seconds"], "thumb_key": key})
        _save_json(f"{base}/vision.json.gz", vision)
        asset.vision_key, asset.people = f"{base}/vision.json.gz", people
        if not asset.preview_key:   # a frame for drawing the gameplay rectangles
            ffmpeg.frame_image(src, asset.duration / 3, work / "preview.jpg", "scale=960:-2")
            storage.put_file(f"{base}/preview.jpg", work / "preview.jpg", "image/jpeg")
            asset.preview_key = f"{base}/preview.jpg"
        db.commit()


def resolve_settings(db, user_id: uuid.UUID, options: dict, work: Path) -> RenderSettings:
    """RenderOptions (as stored on the job/clip) -> concrete RenderSettings
    with files fetched locally. Only the user's own styles/files are used."""
    opts = RenderOptions.model_validate(options or {})
    style = PRESETS.get(opts.style_id.removeprefix("preset:"), PRESETS["clasico"])
    if not opts.style_id.startswith("preset:"):
        try:
            row = db.get(SubtitleStyle, uuid.UUID(opts.style_id))
            if row is not None and row.user_id == user_id:
                style = row.config
        except ValueError:
            pass

    def library_file(item_id, kind) -> Path | None:
        if item_id is None:
            return None
        item = db.get(LibraryItem, item_id)
        if item is None or item.user_id != user_id or item.kind != kind:
            return None
        dest = work / f"{kind}-{item.id}{Path(item.storage_key).suffix}"
        if not dest.exists():
            get_storage().get_file(item.storage_key, dest)
        return dest

    return RenderSettings(
        format=opts.format, layout=opts.layout, framing=opts.framing, subtitles=opts.subtitles,
        style=style, music_path=library_file(opts.music_id, "music"), music_volume=opts.music_volume,
        logo_path=library_file(opts.logo_id, "logo"), logo_position=opts.logo_position,
        gameplay=opts.gameplay.model_dump() if opts.gameplay else None, person=opts.person,
    )


def _render_one(db, clip: Clip, asset: MediaAsset, src: Path, vision: dict | None, transcript: dict | None,
                work: Path, on_progress=None) -> None:
    """Renders `clip` with its own options into a new version and swaps it in."""
    storage = get_storage()
    settings = resolve_settings(db, clip.user_id, clip.render, work)
    meta = {"width": asset.width, "height": asset.height, "fps": asset.fps, "has_video": asset.has_video}
    version = (clip.version or 0) + 1 if clip.storage_key else 1
    base = f"users/{clip.user_id}/clips/{clip.id}/v{version}"
    out, thumb = work / f"{clip.id}-v{version}.mp4", work / f"{clip.id}-v{version}.jpg"
    layout = render_clip(src, clip.start, clip.end, out, settings, meta, vision, transcript, on_progress)
    ffmpeg.thumbnail(out, (clip.end - clip.start) * 0.6, thumb)
    storage.put_file(f"{base}.mp4", out, "video/mp4")
    storage.put_file(f"{base}.jpg", thumb, "image/jpeg")
    out.unlink(missing_ok=True)
    thumb.unlink(missing_ok=True)
    old = (clip.storage_key, clip.thumbnail_key)
    clip.storage_key, clip.thumbnail_key = f"{base}.mp4", f"{base}.jpg"
    clip.layout, clip.version, clip.render_status = layout, version, "ready"
    db.commit()
    for key in old:   # previous version is no longer referenced
        if key and key not in (clip.storage_key, clip.thumbnail_key):
            try:
                storage.delete_prefix(key)
            except Exception:
                log.warning("could not delete %s", key)


def _make_clips(db, job: Job, asset: MediaAsset, prog: Progress, work: Path) -> None:
    existing = list(db.scalars(select(Clip).where(Clip.job_id == job.id)))
    missing = job.clip_count - len(existing)
    if missing <= 0:
        return
    prog.stage("detecting_moments")
    transcript = _load_json(asset.transcript_key, work)
    analysis = _load_json(asset.analysis_key, work)
    vision = _load_json(asset.vision_key, work)
    focus = None
    if job.focus_person is not None and vision:
        focus = person_speaking_seconds(vision, transcript, int(asset.duration) + 1).get(job.focus_person)
        if focus is None:
            raise PipelineError("person_not_found")
    moments = select_moments(analysis, transcript, missing, job.clip_duration, asset.duration,
                             exclude=[(c.start, c.end) for c in existing], focus=focus)
    if not moments and not existing:
        raise PipelineError("no_moments")
    if not moments and focus is not None:
        raise PipelineError("no_person_moments")

    prog.stage("rendering")
    src = _source_file(asset, work)
    done_cb = prog.within("rendering")
    options = dict(job.render or {})
    if job.focus_person is not None:
        options["person"] = job.focus_person   # keep the camera on them
    next_index = max((c.index for c in existing), default=0) + 1
    for i, m in enumerate(moments):
        clip = Clip(job_id=job.id, user_id=job.user_id, index=next_index + i, start=m.start, end=m.end,
                    score=m.score, reasons=m.reasons, text=m.text[:5000], render=options,
                    render_status="rendering", version=0)
        db.add(clip)
        db.commit()
        _render_one(db, clip, asset, src, vision, transcript, work,
                    lambda f, i=i: done_cb((i + f) / len(moments)))
        events.publish(job.id, "clip", ClipOut.model_validate(clip).model_dump(mode="json"))
    if len(existing) + len(moments) < job.clip_count:
        job.clip_count = len(existing) + len(moments)


def _asset_lock(job: Job):
    digest = hashlib.sha256(f"{job.user_id}:{_asset_key(job)}".encode()).hexdigest()[:32]
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
        prog.push(status=JobStatus.done, stage="done", progress=100, focus_person=None)
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
    # Clips left half-made by the failure are marked so the web can offer a retry.
    for c in db.scalars(select(Clip).where(Clip.job_id == job.id, Clip.render_status == "rendering")):
        c.render_status = "failed" if c.storage_key else c.render_status
        if not c.storage_key:
            db.delete(c)
    Progress(db, job).push(status=JobStatus.failed, stage="failed", error=code, focus_person=None)


@celery_app.task(name="app.worker.tasks.rerender_clip", bind=True)
def rerender_clip(self, clip_id: str) -> None:
    """Re-render one clip with new options (layout, format, subtitles, music...)."""
    db = SessionLocal()
    work = Path(get_settings().work_dir) / f"clip-{clip_id}"
    clip = None
    try:
        clip = db.get(Clip, uuid.UUID(clip_id))
        if clip is None or clip.render_status != "rendering":
            return
        job = db.get(Job, clip.job_id)
        asset = db.get(MediaAsset, job.asset_id)
        work.mkdir(parents=True, exist_ok=True)
        src = _source_file(asset, work)
        _render_one(db, clip, asset, src, _load_json(asset.vision_key, work),
                    _load_json(asset.transcript_key, work), work)
    except Exception:
        log.exception("re-render of clip %s failed", clip_id)
        if clip is not None:
            db.rollback()
            clip.render_status = "failed"
            db.commit()
    finally:
        if clip is not None:
            events.publish(clip.job_id, "clip", ClipOut.model_validate(clip).model_dump(mode="json"))
        shutil.rmtree(work, ignore_errors=True)
        db.close()

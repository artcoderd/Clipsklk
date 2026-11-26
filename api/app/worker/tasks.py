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
from app.ai.prefs import record as record_ai, resolve as resolve_ai
from app.ai.select import choose
from app.media.cover import make_cover
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
        if job.source != JobSource.url:   # uploads and live recordings are already stored
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

    if not asset.transcript_key and not job.prepaid:
        # Pay for the processing before the expensive part (transcription). A video
        # already processed (same link, "más clips") is never charged again.
        from app.billing import NoMinutes, charge, minutes_for
        from app.models import User
        try:
            charge(db, db.get(User, job.user_id), minutes_for(asset.duration), f"asset:{asset.id}",
                   f"Video de {minutes_for(asset.duration)} min")
            db.commit()
        except NoMinutes:
            db.rollback()
            raise PipelineError("no_minutes")

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
    edit = _edit_plan(db, clip, work) if clip.edit else None
    version = (clip.version or 0) + 1 if clip.storage_key else 1
    base = f"users/{clip.user_id}/clips/{clip.id}/v{version}"
    out, thumb = work / f"{clip.id}-v{version}.mp4", work / f"{clip.id}-v{version}.jpg"
    layout = render_clip(src, clip.start, clip.end, out, settings, meta, vision, transcript, on_progress, edit=edit)
    dur = edit.total if edit is not None else clip.end - clip.start
    at = clip.cover_t if clip.cover_t is not None and 0 <= clip.cover_t < dur else dur * 0.6
    ffmpeg.thumbnail(out, at, thumb)
    cover = work / f"{clip.id}-v{version}-cover.jpg"
    _cover_from_source(src, clip, at, settings, meta, vision, work, cover)
    storage.put_file(f"{base}.mp4", out, "video/mp4")
    storage.put_file(f"{base}.jpg", thumb, "image/jpeg")
    storage.put_file(f"{base}-cover.jpg", cover, "image/jpeg")
    for f in (out, thumb, cover):
        f.unlink(missing_ok=True)
    old = (clip.storage_key, clip.thumbnail_key, clip.cover_key)
    clip.storage_key, clip.thumbnail_key, clip.cover_key = f"{base}.mp4", f"{base}.jpg", f"{base}-cover.jpg"
    clip.layout, clip.version, clip.render_status = layout, version, "ready"
    db.commit()
    for key in old:   # previous version is no longer referenced
        if key and key not in (clip.storage_key, clip.thumbnail_key, clip.cover_key):
            try:
                storage.delete_prefix(key)
            except Exception:
                log.warning("could not delete %s", key)


def _edit_plan(db, clip: Clip, work: Path):
    """The saved EditDoc with the user's library files fetched locally."""
    from app.media.edit import plan_from_doc

    files: dict[str, Path] = {}
    doc = clip.edit
    ids = {i["library_id"] for i in doc.get("images", [])} | {s["library_id"] for s in doc.get("sounds", [])}
    if doc.get("music"):
        ids.add(doc["music"]["library_id"])
    for item_id in ids:
        item = db.get(LibraryItem, uuid.UUID(item_id))
        if item is None or item.user_id != clip.user_id:
            continue   # deleted from the library since: skipped
        dest = work / f"lib-{item.id}{Path(item.storage_key).suffix}"
        if not dest.exists():
            get_storage().get_file(item.storage_key, dest)
        files[item_id] = dest
    return plan_from_doc(doc, files)


def _cover_from_source(src: Path, clip: Clip, at: float, settings: RenderSettings, meta: dict,
                       vision: dict | None, work: Path, dest: Path) -> None:
    """The cover frame, framed like the clip but without subtitles (only the title goes on it)."""
    from dataclasses import replace

    frame = work / f"{clip.id}-coverframe.mp4"
    t = clip.start + at
    if clip.edit:   # `at` is a position in the edited clip: find its source time
        acc = 0.0
        from app.media.edit import EditPlan
        plan = EditPlan(clip.edit["trim"][0], clip.edit["trim"][1], [tuple(c) for c in clip.edit.get("cuts", [])])
        for a, b in plan.keeps:
            if acc + (b - a) >= at:
                t = a + (at - acc)
                break
            acc += b - a
    plain = replace(settings, subtitles=False, music_path=None)
    try:
        render_clip(src, t, t + 0.2, frame, plain, meta, vision, None)
        make_cover(frame, 0.0, clip.title, settings.format, dest, settings.style)
    finally:
        frame.unlink(missing_ok=True)


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
    provider, rules, hashtags = resolve_ai(db, job.user_id, job.ai)
    if provider:
        prog.push(stage="ai_selecting")
    moments, ai_result, ai_failure = choose(provider, transcript, analysis, missing, job.clip_duration,
                                            asset.duration, [(c.start, c.end) for c in existing], rules,
                                            hashtags, focus=focus)
    record_ai(db, job.user_id, ai_result, "select")
    job.ai_status = (f"ai:{ai_result.provider}" if ai_result else f"fallback:{ai_failure}" if ai_failure
                     else "off" if not provider else "fallback:person_filter")
    db.commit()
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
                    render_status="rendering", version=0, title=m.title[:120], caption=m.caption,
                    hashtags=m.hashtags, cover_t=m.cover_t,
                    ai_provider=ai_result.provider if (m.ai and ai_result) else None)
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
        _notify(db, job.user_id, "Tus clips están listos ✨", job.title or "Proyecto terminado", f"/jobs/{job.id}",
                f"job-{job.id}")
    except PipelineError as e:
        log.warning("job %s failed: %s", job_id, e)
        _fail(db, job, e.code)
    except Exception:
        log.exception("job %s crashed", job_id)
        _fail(db, job, "processing_failed")
    finally:
        shutil.rmtree(work, ignore_errors=True)
        db.close()


def _refund_unprocessed(db, job: Job) -> None:
    """Charged but never transcribed (the job failed): give the minutes back."""
    from app.billing import grant
    from app.models import CreditEntry

    asset = db.get(MediaAsset, job.asset_id) if job.asset_id else None
    if asset is None or asset.transcript_key:
        return
    paid = db.scalars(select(CreditEntry).where(CreditEntry.ref.in_((f"asset:{asset.id}", f"asset:{asset.id}:credits")))).all()
    total = -sum(e.minutes for e in paid)
    if total > 0 and grant(db, job.user_id, total, "refund", f"refund:asset:{asset.id}", "Devolución: el video falló"):
        db.commit()


def _fail(db, job: Job | None, code: str) -> None:
    if job is None:
        return
    db.rollback()
    _refund_unprocessed(db, job)
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
        asset = db.get(MediaAsset, clip.asset_id or job.asset_id)
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


def _notify(db, user_id, title: str, body: str, url: str, tag: str) -> None:
    try:
        from app.push import send_to_user
        send_to_user(db, user_id, title, body, url, tag)
    except Exception:
        log.warning("push notification failed", exc_info=True)


# --- Live -------------------------------------------------------------------

@celery_app.task(name="app.worker.tasks.run_live", bind=True, acks_late=False,
                 time_limit=int(get_settings().live_max_hours * 3600 + 3600))
def run_live(self, session_id: str) -> None:
    """Records and watches one live stream until it ends (runs for hours).
    acks_late=False: a long recording must never be re-delivered to a second worker."""
    from app.live.controller import LiveController
    from app.models import LiveSession

    try:
        LiveController(session_id).run()
    except Exception:
        log.exception("live %s crashed", session_id)
        db = SessionLocal()
        try:
            live = db.get(LiveSession, uuid.UUID(session_id))
            if live is not None:
                live.status, live.error = "failed", "live_crashed"
                db.commit()
        finally:
            db.close()


def _snap(t: float, edges: list[float], limit: float) -> float:
    near = [e for e in edges if abs(e - t) <= limit]
    return min(near, key=lambda e: abs(e - t)) if near else t


@celery_app.task(name="app.worker.tasks.make_live_clip", bind=True)
def make_live_clip(self, clip_id: str) -> None:
    """Renders a clip cut from a live stream: transcribe + faces on its own
    short source, tidy the edges to sentence boundaries, render, notify."""
    from app.live.controller import publish as live_publish
    from app.models import LiveSession

    db = SessionLocal()
    work = Path(get_settings().work_dir) / f"live-{clip_id}"
    clip = None
    try:
        clip = db.get(Clip, uuid.UUID(clip_id))
        if clip is None or clip.render_status != "rendering":
            return
        asset = db.get(MediaAsset, clip.asset_id)
        work.mkdir(parents=True, exist_ok=True)
        src = _source_file(asset, work)
        meta = ffmpeg.probe(src)
        asset.duration, asset.width, asset.height, asset.has_video, asset.fps = (
            meta["duration"], meta["width"], meta["height"], meta["has_video"], meta["fps"])
        base = f"users/{clip.user_id}/assets/{asset.id}"
        if asset.transcript_key:   # the AI review already transcribed this stretch
            transcript = _load_json(asset.transcript_key, work)
        else:
            wav = work / "audio.wav"
            ffmpeg.extract_audio(src, wav, meta["duration"])
            transcript = transcribe(wav, meta["duration"])
            _save_json(f"{base}/transcript.json.gz", transcript)
            asset.transcript_key = f"{base}/transcript.json.gz"
        vision = _load_json(asset.vision_key, work) if asset.vision_key else None
        if meta["has_video"] and vision is None:
            vision = analyze_video(src, meta["width"], meta["height"], meta["duration"],
                                   hwaccel=ffmpeg.video_encoder() == "h264_nvenc")
            _save_json(f"{base}/vision.json.gz", vision)
            asset.vision_key = f"{base}/vision.json.gz"
        # Start and end on sentence boundaries when one is close.
        starts = [sg["start"] for sg in transcript["segments"]]
        ends = [sg["end"] for sg in transcript["segments"]]
        a = _snap(clip.start, starts, 3.0)
        b = _snap(clip.end, ends, 3.0) if "clip ahora" not in clip.reasons else clip.end
        if b - a >= 3:
            clip.start, clip.end = round(max(0.0, a), 2), round(min(meta["duration"], b), 2)
        clip.text = " ".join(w["word"].strip() for sg in transcript["segments"] for w in (sg.get("words") or [])
                             if clip.start <= w["start"] < clip.end)[:5000]
        if not clip.title:
            _caption_clip(db, clip)
        db.commit()
        _render_one(db, clip, asset, src, vision, transcript, work)
        live = db.query(LiveSession).filter(LiveSession.job_id == clip.job_id).first()
        url = f"/live/{live.id}" if live else f"/jobs/{clip.job_id}"
        _notify(db, clip.user_id, "¡Nuevo clip del directo! 🎬",
                ", ".join(clip.reasons) if clip.reasons else "Listo para publicar", url, f"clip-{clip.id}")
    except Exception:
        log.exception("live clip %s failed", clip_id)
        if clip is not None:
            db.rollback()
            clip.render_status = "failed"
            db.commit()
    finally:
        if clip is not None:
            payload = ClipOut.model_validate(clip).model_dump(mode="json")
            events.publish(clip.job_id, "clip", payload)
            live = db.query(LiveSession).filter(LiveSession.job_id == clip.job_id).first()
            if live:
                live_publish(live.id, "clip", payload)
        shutil.rmtree(work, ignore_errors=True)
        db.close()


def _caption_clip(db, clip: Clip) -> None:
    """Title, caption and hashtags: AI when enabled, the clip's own words otherwise."""
    from app.ai import AIError
    from app.ai.captions import ai_caption, heuristic_caption

    job = db.get(Job, clip.job_id)
    provider, rules, hashtags = resolve_ai(db, clip.user_id, job.ai if job else None)
    if provider and clip.text:
        try:
            cap, result = ai_caption(provider, clip.text, rules, hashtags)
            record_ai(db, clip.user_id, result, "caption")
            clip.title, clip.caption, clip.hashtags = cap.title[:120], cap.caption, cap.hashtags
            clip.ai_provider = result.provider
            return
        except AIError as e:
            log.info("AI caption failed (%s); using the clip's words", e.code)
    clip.title, clip.caption, clip.hashtags = heuristic_caption(clip.text, hashtags)


@celery_app.task(name="app.worker.tasks.refresh_cover", bind=True)
def refresh_cover(self, clip_id: str) -> None:
    """Redraws the cover after the user edits the title (no re-render)."""
    db = SessionLocal()
    work = Path(get_settings().work_dir) / f"cover-{clip_id}"
    try:
        clip = db.get(Clip, uuid.UUID(clip_id))
        if clip is None or not clip.storage_key:
            return
        work.mkdir(parents=True, exist_ok=True)
        storage = get_storage()
        job = db.get(Job, clip.job_id)
        asset = db.get(MediaAsset, clip.asset_id or job.asset_id)
        src = _source_file(asset, work)
        settings = resolve_settings(db, clip.user_id, clip.render, work)
        meta = {"width": asset.width, "height": asset.height, "fps": asset.fps, "has_video": asset.has_video}
        dur = clip.end - clip.start
        at = clip.cover_t if clip.cover_t is not None and 0 <= clip.cover_t < dur else dur * 0.6
        out = work / "cover.jpg"
        _cover_from_source(src, clip, at, settings, meta, _load_json(asset.vision_key, work), work, out)
        key = f"users/{clip.user_id}/clips/{clip.id}/v{clip.version}-cover-{uuid.uuid4().hex[:6]}.jpg"
        storage.put_file(key, out, "image/jpeg")
        old, clip.cover_key = clip.cover_key, key
        db.commit()
        if old and old != key:
            storage.delete_prefix(old)
        events.publish(clip.job_id, "clip", ClipOut.model_validate(clip).model_dump(mode="json"))
    finally:
        shutil.rmtree(work, ignore_errors=True)
        db.close()


@celery_app.task(name="app.worker.tasks.live_ai_review", bind=True)
def live_ai_review(self, session_id: str, asset_id: str, offset: float) -> None:
    """Live: the AI reads the last few minutes and proposes a clip if there is
    a genuinely good moment (it may propose none)."""
    from app.ai.select import ai_select
    from app.models import LiveSession

    db = SessionLocal()
    work = Path(get_settings().work_dir) / f"review-{asset_id}"
    made = []
    try:
        live = db.get(LiveSession, uuid.UUID(session_id))
        asset = db.get(MediaAsset, uuid.UUID(asset_id))
        if live is None or asset is None:
            return
        provider, rules, hashtags = resolve_ai(db, live.user_id, (db.get(Job, live.job_id).ai or {}))
        if not provider:
            return
        work.mkdir(parents=True, exist_ok=True)
        src = _source_file(asset, work)
        meta = ffmpeg.probe(src)
        asset.duration, asset.width, asset.height, asset.has_video, asset.fps = (
            meta["duration"], meta["width"], meta["height"], meta["has_video"], meta["fps"])
        wav = work / "audio.wav"
        ffmpeg.extract_audio(src, wav, meta["duration"])
        transcript = transcribe(wav, meta["duration"])
        base = f"users/{live.user_id}/assets/{asset.id}"
        _save_json(f"{base}/transcript.json.gz", transcript)
        asset.transcript_key = f"{base}/transcript.json.gz"
        analysis = analyze(wav)
        # Ranges already clipped in this broadcast, relative to this window.
        existing = [(c.offset + c.start - offset, c.offset + c.end - offset)
                    for c in db.query(Clip).filter(Clip.job_id == live.job_id).all()]
        exclude = [(a, b) for a, b in existing if b > 0 and a < meta["duration"]]
        only_good = rules + ("\n" if rules else "") + (
            "Directo en vivo: propone como mucho 1 clip y solo si hay un momento realmente bueno; "
            "si no, devuelve la lista vacía.")
        try:
            moments, result = ai_select(provider, transcript, analysis, 1, live.clip_duration, meta["duration"],
                                        exclude, only_good, hashtags)
            record_ai(db, live.user_id, result, "live")
        except Exception as e:
            log.info("live AI review skipped: %s", e)
            moments = []
        job = db.get(Job, live.job_id)
        for m in moments:
            index = db.query(Clip).filter(Clip.job_id == job.id).count() + 1
            clip = Clip(job_id=job.id, user_id=live.user_id, index=index, asset_id=asset.id, offset=offset,
                        start=m.start, end=m.end, score=m.score, reasons=m.reasons, text=m.text[:5000],
                        render=dict(job.render or {}), render_status="rendering", version=0, title=m.title,
                        caption=m.caption, hashtags=m.hashtags, cover_t=m.cover_t, ai_provider=provider)
            db.add(clip)
            job.clip_count = index
            made.append(clip)
        db.commit()
        if not made:   # nothing worth a clip: drop this stretch to save storage
            get_storage().delete_prefix(asset.storage_key)
            get_storage().delete_prefix(base)
            db.delete(asset)
            db.commit()
    finally:
        shutil.rmtree(work, ignore_errors=True)
        db.close()
    for clip in made:
        make_live_clip.run(str(clip.id))


@celery_app.task(name="app.worker.tasks.prepare_proxy", bind=True)
def prepare_proxy(self, clip_id: str) -> None:
    """Editor preview: the clip's stretch (+ margin) framed like the clip, half
    size, no subtitles/music/logo (the browser draws those live)."""
    from dataclasses import replace

    db = SessionLocal()
    work = Path(get_settings().work_dir) / f"proxy-{clip_id}"
    clip = None
    try:
        clip = db.get(Clip, uuid.UUID(clip_id))
        if clip is None or clip.proxy_status != "preparing":
            return
        job = db.get(Job, clip.job_id)
        asset = db.get(MediaAsset, clip.asset_id or job.asset_id)
        work.mkdir(parents=True, exist_ok=True)
        src = _source_file(asset, work)
        settings = replace(resolve_settings(db, clip.user_id, clip.render, work), subtitles=False, music_path=None,
                           logo_path=None, scale=0.5)
        meta = {"width": asset.width, "height": asset.height, "fps": asset.fps, "has_video": asset.has_video}
        out = work / "proxy.mp4"
        render_clip(src, clip.proxy_start, clip.proxy_end, out, settings, meta,
                    _load_json(asset.vision_key, work), None)
        key = f"users/{clip.user_id}/clips/{clip.id}/proxy-{clip.proxy_sig}.mp4"
        get_storage().put_file(key, out, "video/mp4")
        old, clip.proxy_key, clip.proxy_status = clip.proxy_key, key, "ready"
        db.commit()
        if old and old != key:
            get_storage().delete_prefix(old)
    except Exception:
        log.exception("proxy for clip %s failed", clip_id)
        if clip is not None:
            db.rollback()
            clip.proxy_status = "failed"
            db.commit()
    finally:
        if clip is not None:
            events.publish(clip.job_id, "clip", ClipOut.model_validate(clip).model_dump(mode="json"))
        shutil.rmtree(work, ignore_errors=True)
        db.close()


# --- Publishing -------------------------------------------------------------

PLATFORM_LABEL = {"facebook": "Facebook", "instagram": "Instagram", "tiktok": "TikTok", "telegram": "Telegram"}
MAX_ATTEMPTS = 3


@celery_app.task(name="app.worker.tasks.publish_post", bind=True)
def publish_post(self, publication_id: str) -> None:
    """Uploads one clip to one account. Temporary platform errors are retried
    (up to 3 attempts, a few minutes apart); an account whose permission was
    revoked is marked so the user can reconnect it."""
    from datetime import datetime, timezone

    from app.models import Publication, SocialAccount
    from app.social import meta, telegram, tiktok
    from app.social.http import PlatformError
    from app.social.tokens import access_token

    db = SessionLocal()
    work = Path(get_settings().work_dir) / f"pub-{publication_id}"
    pub = None
    try:
        pub = db.get(Publication, uuid.UUID(publication_id))
        if pub is None or pub.status != "publishing":
            return
        acc = db.get(SocialAccount, pub.account_id)
        clip = db.get(Clip, pub.clip_id)
        pub.attempts += 1
        db.commit()
        storage = get_storage()
        video = storage.local_path(clip.storage_key)
        if video is None:
            work.mkdir(parents=True, exist_ok=True)
            video = work / "clip.mp4"
            storage.get_file(clip.storage_key, video)
        try:
            token = access_token(db, acc)
            if acc.platform == "facebook":
                res = meta.publish_facebook(token, acc.meta["page_id"], video, pub.caption)
            elif acc.platform == "instagram":
                cover_ms = int(clip.cover_t * 1000) if clip.cover_t is not None else None
                res = meta.publish_instagram(token, acc.meta["ig_id"], video, pub.caption, cover_ms)
            elif acc.platform == "tiktok":
                res = tiktok.publish(token, video, pub.caption, pub.options or {})
            elif acc.platform == "telegram":
                res = telegram.publish(token, acc.meta["chat_id"], video, pub.caption)
            else:
                raise PlatformError("unknown_platform", acc.platform)
        except PlatformError as e:
            if e.code == "account_expired":
                acc.status, acc.last_error = "expired", e.detail[:500]
            if e.retry and pub.attempts < MAX_ATTEMPTS:
                pub.error = e.code
                db.commit()
                self.apply_async(args=[publication_id], countdown=120 * pub.attempts)
                return
            pub.status, pub.error = "failed", e.code
            db.commit()
            _notify(db, pub.user_id, f"No se pudo publicar en {PLATFORM_LABEL.get(acc.platform, acc.platform)}",
                    acc.name, f"/jobs/{clip.job_id}", f"pub-{pub.id}")
            return
        pub.status = "draft_sent" if res.get("draft") else "published"
        pub.external_id, pub.external_url = res.get("id"), res.get("url")
        pub.published_at, pub.error = datetime.now(timezone.utc), None
        db.commit()
        verb = "Enviado a tus borradores de" if res.get("draft") else "Publicado en"
        _notify(db, pub.user_id, f"{verb} {PLATFORM_LABEL.get(acc.platform, acc.platform)} ✓", acc.name,
                pub.external_url or f"/jobs/{clip.job_id}", f"pub-{pub.id}")
    except Exception:
        log.exception("publication %s crashed", publication_id)
        if pub is not None:
            db.rollback()
            pub.status, pub.error = "failed", "publish_crashed"
            db.commit()
    finally:
        shutil.rmtree(work, ignore_errors=True)
        db.close()


@celery_app.task(name="app.worker.tasks.dispatch_due")
def dispatch_due() -> int:
    """Runs every minute (celery beat): starts scheduled publications whose time came."""
    from datetime import datetime, timezone

    from app.models import Publication
    from app.worker import enqueue_publish

    db = SessionLocal()
    try:
        due = db.scalars(select(Publication).where(Publication.status == "scheduled",
                                                   Publication.scheduled_at <= datetime.now(timezone.utc))
                         .with_for_update(skip_locked=True)).all()
        for pub in due:
            pub.status = "publishing"
        db.commit()
        for pub in due:
            enqueue_publish(pub.id)
        return len(due)
    finally:
        db.close()

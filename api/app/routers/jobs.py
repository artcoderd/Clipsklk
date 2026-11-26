import asyncio
import json
import uuid
from pathlib import PurePath

from fastapi import (APIRouter, Depends, File, Form, HTTPException, Request, UploadFile,
                     status)
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import activity, events
from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user
from app.models import Clip, Job, JobSource, JobStatus, MediaAsset, User
from app.platforms import detect_platform
from app.redis_client import get_redis
from pydantic import ValidationError

from app.render_options import validate_render
from app.routers.clips import _serve
from app.schemas import AIOptions, ClipOut, JobFromUrlIn, JobOut, MoreClipsIn, PersonOut, RenderOptions
from app.storage import StorageError, get_storage
from app.worker import enqueue_job

router = APIRouter(prefix="/jobs", tags=["jobs"])

FINAL = {"done", "failed", "canceled"}
ALLOWED_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".mp3", ".m4a", ".wav"}


def _check_quota(db: Session, user: User, new_video: bool = True) -> None:
    from app.billing import balance, plan_of, unlimited

    active = db.scalar(select(func.count()).select_from(Job).where(
        Job.user_id == user.id, Job.status.in_([JobStatus.queued, JobStatus.running]), Job.source != JobSource.live))
    limit = get_settings().max_active_jobs_per_user if unlimited(user) else plan_of(db, user.id).max_active_jobs
    if active >= limit:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too_many_active_jobs")
    if new_video and not unlimited(user) and balance(db, user.id).total_left < 1:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, "no_minutes")


def _own_job(db: Session, user: User, job_id: uuid.UUID) -> Job:
    job = db.get(Job, job_id)
    # Users only ever see their own jobs (404, not 403, to avoid leaking ids).
    if job is None or job.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return job


@router.post("", response_model=JobOut, status_code=201)
def create_from_url(data: JobFromUrlIn, request: Request, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    platform = detect_platform(data.url)
    if platform is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unsupported_url")
    _check_quota(db, user)
    job = Job(user_id=user.id, source=JobSource.url, source_url=data.url.strip(), platform=platform,
              clip_count=data.clip_count, clip_duration=data.clip_duration,
              render=validate_render(db, user.id, data.render), ai=data.ai.model_dump())
    db.add(job)
    db.flush()
    activity.log(db, "job_created", user.id, request, job=str(job.id), source="url", platform=platform)
    db.commit()
    enqueue_job(job.id)
    return job


@router.post("/upload", response_model=JobOut, status_code=201)
def create_from_upload(request: Request, file: UploadFile = File(...), clip_count: int = Form(5),
                       clip_duration: int = Form(45), render: str = Form("{}"), ai: str = Form("{}"),
                       user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not 1 <= clip_count <= 30 or not 10 <= clip_duration <= 180:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_clip_options")
    try:
        render_opts = validate_render(db, user.id, RenderOptions.model_validate_json(render or "{}"))
        ai_opts = AIOptions.model_validate_json(ai or "{}").model_dump()
    except ValidationError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_render_options")
    name = PurePath(file.filename or "video").name[:255]
    ext = PurePath(name).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unsupported_file_type")
    _check_quota(db, user)

    job = Job(user_id=user.id, source=JobSource.upload, original_filename=name,
              clip_count=clip_count, clip_duration=clip_duration, render=render_opts, ai=ai_opts)
    db.add(job)
    db.flush()
    key = f"users/{user.id}/jobs/{job.id}/source{ext}"
    try:
        get_storage().save_stream(key, file.file, get_settings().max_upload_mb * 1024 * 1024)
    except StorageError as e:
        db.rollback()
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, str(e))
    job.storage_key = key
    activity.log(db, "job_created", user.id, request, job=str(job.id), source="upload", filename=name)
    db.commit()
    enqueue_job(job.id)
    return job


@router.get("", response_model=list[JobOut])
def list_jobs(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.scalars(select(Job).where(Job.user_id == user.id)
                      .order_by(Job.created_at.desc()).limit(50)).all()


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _own_job(db, user, job_id)


@router.get("/{job_id}/clips", response_model=list[ClipOut])
def list_clips(job_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _own_job(db, user, job_id)
    return db.scalars(select(Clip).where(Clip.job_id == job_id, Clip.storage_key.is_not(None))
                      .order_by(Clip.score.desc(), Clip.index)).all()


@router.post("/{job_id}/more", response_model=JobOut)
def more_clips(job_id: uuid.UUID, data: MoreClipsIn, request: Request,
               user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """More clips from the same video: reuses the stored source, transcript and
    analysis, so nothing is downloaded or transcribed again."""
    job = _own_job(db, user, job_id)
    if job.status in (JobStatus.queued, JobStatus.running):
        raise HTTPException(status.HTTP_409_CONFLICT, "job_busy")
    if job.asset_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "job_not_ready")
    _check_quota(db, user, new_video=False)
    if data.person is not None:
        asset = db.get(MediaAsset, job.asset_id)
        if not any(p["id"] == data.person for p in (asset.people or [])):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "person_not_found")
    done = db.scalar(select(func.count()).select_from(Clip).where(Clip.job_id == job.id))
    job.clip_count = done + data.count
    job.focus_person = data.person
    job.status, job.stage, job.progress, job.error = JobStatus.queued, "queued", 75, None
    activity.log(db, "job_more_clips", user.id, request, job=str(job.id), count=data.count)
    db.commit()
    enqueue_job(job.id)
    return job


def _asset(db: Session, job: Job) -> MediaAsset:
    asset = db.get(MediaAsset, job.asset_id) if job.asset_id else None
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return asset


@router.get("/{job_id}/people", response_model=list[PersonOut])
def list_people(job_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """People seen in the video (most visible first), to make clips of just one."""
    job = _own_job(db, user, job_id)
    if not job.asset_id:
        return []
    return [PersonOut(id=p["id"], seconds=p["seconds"]) for p in (_asset(db, job).people or [])
            if p.get("thumb_key")]


@router.get("/{job_id}/people/{person_id}/thumbnail")
def person_thumbnail(job_id: uuid.UUID, person_id: int, user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    asset = _asset(db, _own_job(db, user, job_id))
    p = next((p for p in (asset.people or []) if p["id"] == person_id and p.get("thumb_key")), None)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return _serve(p["thumb_key"], "image/jpeg")


@router.get("/{job_id}/preview")
def preview_frame(job_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """A frame of the source video, to draw the gameplay camera/game areas on."""
    asset = _asset(db, _own_job(db, user, job_id))
    if not asset.preview_key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return _serve(asset.preview_key, "image/jpeg")


@router.get("/{job_id}/events")
async def job_events(job_id: uuid.UUID, request: Request, user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    """Server-Sent Events stream with live progress for one job."""
    _own_job(db, user, job_id)  # 404 unless it belongs to this user
    # Subscribe BEFORE reading the snapshot so no update published in between is lost.
    pubsub = get_redis().pubsub()
    pubsub.subscribe(events.channel(job_id))
    db.expire_all()
    snapshot = JobOut.model_validate(db.get(Job, job_id)).model_dump(mode="json")
    db.close()
    first = json.dumps({"type": "job", "job": snapshot})

    async def stream():
        try:
            yield f"data: {first}\n\n"
            if snapshot["status"] in FINAL:
                return
            idle = 0.0
            while not await request.is_disconnected():
                msg = await asyncio.to_thread(pubsub.get_message, ignore_subscribe_messages=True, timeout=1.0)
                if msg and msg.get("type") == "message":
                    idle = 0.0
                    yield f"data: {msg['data']}\n\n"
                    if json.loads(msg["data"]).get("job", {}).get("status") in FINAL:
                        return
                else:
                    idle += 1.0
                    if idle >= 15:
                        idle = 0.0
                        yield ": keep-alive\n\n"
        finally:
            pubsub.close()

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})

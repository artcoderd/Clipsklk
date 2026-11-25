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
from app.models import Job, JobSource, JobStatus, User
from app.platforms import detect_platform
from app.redis_client import get_redis
from app.schemas import JobFromUrlIn, JobOut
from app.storage import StorageError, get_storage
from app.worker import enqueue_job

router = APIRouter(prefix="/jobs", tags=["jobs"])

FINAL = {"done", "failed", "canceled"}
ALLOWED_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".mp3", ".m4a", ".wav"}


def _check_quota(db: Session, user: User) -> None:
    active = db.scalar(select(func.count()).select_from(Job).where(
        Job.user_id == user.id, Job.status.in_([JobStatus.queued, JobStatus.running])))
    if active >= get_settings().max_active_jobs_per_user:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too_many_active_jobs")


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
              clip_count=data.clip_count, clip_duration=data.clip_duration)
    db.add(job)
    db.flush()
    activity.log(db, "job_created", user.id, request, job=str(job.id), source="url", platform=platform)
    db.commit()
    enqueue_job(job.id)
    return job


@router.post("/upload", response_model=JobOut, status_code=201)
def create_from_upload(request: Request, file: UploadFile = File(...), clip_count: int = Form(5),
                       clip_duration: int = Form(45), user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    if not 1 <= clip_count <= 30 or not 10 <= clip_duration <= 180:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_clip_options")
    name = PurePath(file.filename or "video").name[:255]
    ext = PurePath(name).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unsupported_file_type")
    _check_quota(db, user)

    job = Job(user_id=user.id, source=JobSource.upload, original_filename=name,
              clip_count=clip_count, clip_duration=clip_duration)
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

    async def stream():
        try:
            yield f"data: {json.dumps(snapshot)}\n\n"
            if snapshot["status"] in FINAL:
                return
            idle = 0.0
            while not await request.is_disconnected():
                msg = await asyncio.to_thread(pubsub.get_message, ignore_subscribe_messages=True, timeout=1.0)
                if msg and msg.get("type") == "message":
                    idle = 0.0
                    yield f"data: {msg['data']}\n\n"
                    if json.loads(msg["data"]).get("status") in FINAL:
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

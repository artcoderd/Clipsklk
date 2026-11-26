import asyncio
import json
import time
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import activity
from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user
from app.live_keys import command_key, live_channel
from app.models import Job, JobSource, JobStatus, LiveSession, User
from app.platforms import detect_platform
from app.redis_client import get_redis
from app.render_options import validate_render
from app.schemas import ClipNowIn, LiveIn, LiveOut, LiveUpdateIn
from app.worker import enqueue_live

router = APIRouter(prefix="/lives", tags=["live"])

ACTIVE = ("starting", "waiting", "recording", "reconnecting")
LIVE_PLATFORMS = {"youtube", "twitch", "kick", "tiktok"}
STALE = timedelta(minutes=2)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _check_stale(db: Session, live: LiveSession) -> LiveSession:
    """A session whose worker vanished (crash, server restart) is shown as failed."""
    if live.status in ACTIVE and datetime.now(timezone.utc) - _aware(live.heartbeat_at) > STALE \
            and datetime.now(timezone.utc) - _aware(live.started_at) > STALE:
        live.status, live.error = "failed", "live_lost"
        db.commit()
    return live


def _own(db: Session, user: User, live_id: uuid.UUID) -> LiveSession:
    live = db.get(LiveSession, live_id)
    if live is None or live.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return _check_stale(db, live)


def _command(live_id, payload: dict) -> None:
    r = get_redis()
    r.rpush(command_key(live_id), json.dumps(payload))
    r.expire(command_key(live_id), 3600)


@router.post("", response_model=LiveOut, status_code=201)
def start_live(data: LiveIn, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    url = data.url.strip()
    platform = detect_platform(url)
    if platform not in LIVE_PLATFORMS:
        if not (get_settings().live_allow_generic_hls and url.split("?")[0].endswith(".m3u8")):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "unsupported_live_url")
        platform = "hls"
    for live in db.scalars(select(LiveSession).where(LiveSession.user_id == user.id,
                                                     LiveSession.status.in_(ACTIVE))):
        _check_stale(db, live)
    active = db.scalar(select(func.count()).select_from(LiveSession).where(
        LiveSession.user_id == user.id, LiveSession.status.in_(ACTIVE)))
    if active >= get_settings().max_lives_per_user:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too_many_lives")
    render = validate_render(db, user.id, data.render)
    job = Job(user_id=user.id, source=JobSource.live, source_url=url, platform=platform, title="Directo",
              clip_count=0, clip_duration=data.clip_duration, render=render, status=JobStatus.running,
              stage="live", progress=0)
    db.add(job)
    db.flush()
    live = LiveSession(user_id=user.id, url=url, platform=platform, auto_clips=data.auto_clips,
                       sensitivity=data.sensitivity, clip_duration=data.clip_duration,
                       final_clips=data.final_clips, render=render, job_id=job.id,
                       chat_status="none")
    db.add(live)
    db.flush()
    activity.log(db, "live_started", user.id, request, live=str(live.id), platform=platform)
    db.commit()
    enqueue_live(live.id)
    return live


@router.get("", response_model=list[LiveOut])
def list_lives(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(LiveSession).where(LiveSession.user_id == user.id)
                      .order_by(LiveSession.started_at.desc()).limit(30)).all()
    return [_check_stale(db, r) for r in rows]


@router.get("/{live_id}", response_model=LiveOut)
def get_live(live_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _own(db, user, live_id)


@router.post("/{live_id}/clip", status_code=202)
def clip_now(live_id: uuid.UUID, data: ClipNowIn, user: User = Depends(get_current_user),
             db: Session = Depends(get_db)):
    """"¡Clip ahora!": the last `seconds` of the stream, counted from this tap."""
    live = _own(db, user, live_id)
    if live.status not in ACTIVE:
        raise HTTPException(status.HTTP_409_CONFLICT, "live_not_active")
    _command(live.id, {"type": "clip_now", "wall": time.time(), "seconds": data.seconds})
    return {"ok": True}


@router.patch("/{live_id}", response_model=LiveOut)
def update_live(live_id: uuid.UUID, data: LiveUpdateIn, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    live = _own(db, user, live_id)
    cmd = {"type": "settings"}
    if data.auto_clips is not None:
        live.auto_clips = cmd["auto_clips"] = data.auto_clips
    if data.sensitivity is not None:
        live.sensitivity = cmd["sensitivity"] = data.sensitivity
    db.commit()
    if live.status in ACTIVE:
        _command(live.id, cmd)
    return live


@router.post("/{live_id}/stop", response_model=LiveOut)
def stop_live(live_id: uuid.UUID, request: Request, user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    live = _own(db, user, live_id)
    if live.status in ACTIVE:
        _command(live.id, {"type": "stop"})
        activity.log(db, "live_stopped", user.id, request, live=str(live.id))
        db.commit()
    return live


@router.get("/{live_id}/events")
async def live_events(live_id: uuid.UUID, request: Request, user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    """SSE: session status, activity (for the chart) and new clips."""
    live = _own(db, user, live_id)
    pubsub = get_redis().pubsub()
    pubsub.subscribe(live_channel(live.id))
    first = json.dumps({"type": "live", "live": LiveOut.model_validate(live).model_dump(mode="json")})
    db.close()

    async def stream():
        try:
            yield f"data: {first}\n\n"
            idle = 0.0
            while not await request.is_disconnected():
                msg = await asyncio.to_thread(pubsub.get_message, ignore_subscribe_messages=True, timeout=1.0)
                if msg and msg.get("type") == "message":
                    idle = 0.0
                    yield f"data: {msg['data']}\n\n"
                else:
                    idle += 1.0
                    if idle >= 15:
                        idle = 0.0
                        yield ": keep-alive\n\n"
        finally:
            pubsub.close()

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})

import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app import activity
from app.models import Clip, Job, MediaAsset, User
from app.render_options import validate_render
from app.schemas import ClipOut, RenderOptions
from app.storage import get_storage
from app.worker import enqueue_rerender

router = APIRouter(prefix="/clips", tags=["clips"])


def _own_clip(db: Session, user: User, clip_id: uuid.UUID) -> Clip:
    clip = db.get(Clip, clip_id)
    if clip is None or clip.user_id != user.id or clip.storage_key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return clip


def _download_name(db: Session, clip: Clip) -> str:
    job = db.get(Job, clip.job_id)
    base = re.sub(r"[^\w\-]+", "-", (job.title or "clipeo") if job else "clipeo", flags=re.UNICODE)
    return f"{base.strip('-')[:60] or 'clipeo'}-clip{clip.index}.mp4"


def _serve(key: str, media_type: str, download_name: str | None = None):
    storage = get_storage()
    url = storage.url(key, download_name)
    if url:  # S3/R2: the browser fetches straight from the bucket/CDN (presigned, expiring)
        return RedirectResponse(url, status_code=307)
    path = storage.local_path(key)
    if path is None or not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    # FileResponse supports HTTP Range, so the player can seek and iOS can stream.
    return FileResponse(path, media_type=media_type, filename=download_name,
                        content_disposition_type="attachment" if download_name else "inline",
                        headers={"Cache-Control": "private, max-age=3600"})


@router.get("/{clip_id}/video")
def clip_video(clip_id: uuid.UUID, download: bool = False, user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    clip = _own_clip(db, user, clip_id)
    return _serve(clip.storage_key, "video/mp4", _download_name(db, clip) if download else None)


@router.get("/{clip_id}/thumbnail")
def clip_thumbnail(clip_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    clip = _own_clip(db, user, clip_id)
    if not clip.thumbnail_key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return _serve(clip.thumbnail_key, "image/jpeg")


@router.get("/{clip_id}", response_model=ClipOut)
def get_clip(clip_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _own_clip(db, user, clip_id)


@router.post("/{clip_id}/render", response_model=ClipOut)
def rerender(clip_id: uuid.UUID, opts: RenderOptions, request: Request, user: User = Depends(get_current_user),
             db: Session = Depends(get_db)):
    """Render this clip again with other options (format, layout, subtitles, music...)."""
    clip = _own_clip(db, user, clip_id)
    if clip.render_status == "rendering":
        raise HTTPException(status.HTTP_409_CONFLICT, "clip_busy")
    job = db.get(Job, clip.job_id)
    asset_id = clip.asset_id or (job.asset_id if job else None)
    asset = db.get(MediaAsset, asset_id) if asset_id else None
    if asset is None or not asset.storage_key:
        raise HTTPException(status.HTTP_409_CONFLICT, "job_not_ready")
    clip.render = validate_render(db, user.id, opts)
    clip.render_status = "rendering"
    activity.log(db, "clip_rerender", user.id, request, clip=str(clip.id))
    db.commit()
    enqueue_rerender(clip.id)
    return clip

"""Each user's logos (watermark) and background music."""
import uuid
from pathlib import PurePath

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import activity
from app.db import get_db
from app.deps import get_current_user
from app.models import LibraryItem, User
from app.routers.clips import _serve
from app.schemas import LibraryItemOut
from app.storage import StorageError, get_storage

router = APIRouter(prefix="/library", tags=["library"])

KINDS = {
    "logo": ({".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}, 5),
    "music": ({".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".wav": "audio/wav", ".ogg": "audio/ogg"}, 40),
    # Editor: stickers/pictures over the video, and short sound effects.
    "image": ({".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}, 10),
    "sound": ({".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".wav": "audio/wav", ".ogg": "audio/ogg"}, 10),
}
MAX_ITEMS = 50


@router.get("", response_model=list[LibraryItemOut])
def list_items(kind: str | None = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = select(LibraryItem).where(LibraryItem.user_id == user.id)
    if kind:
        q = q.where(LibraryItem.kind == kind)
    return db.scalars(q.order_by(LibraryItem.created_at.desc())).all()


@router.post("", response_model=LibraryItemOut, status_code=201)
def upload_item(request: Request, kind: str = Form(...), file: UploadFile = File(...), name: str = Form(""),
                user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if kind not in KINDS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_kind")
    types, max_mb = KINDS[kind]
    filename = PurePath(file.filename or kind).name
    ext = PurePath(filename).suffix.lower()
    if ext not in types:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unsupported_file_type")
    if db.scalar(select(func.count()).select_from(LibraryItem).where(LibraryItem.user_id == user.id)) >= MAX_ITEMS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "library_full")
    item_id = uuid.uuid4()
    key = f"users/{user.id}/library/{item_id}{ext}"
    try:
        size = get_storage().save_stream(key, file.file, max_mb * 1024 * 1024)
    except StorageError as e:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, str(e))
    item = LibraryItem(id=item_id, user_id=user.id, kind=kind, name=(name or PurePath(filename).stem)[:120],
                       storage_key=key, content_type=types[ext], size=size)
    db.add(item)
    activity.log(db, "library_upload", user.id, request, kind=kind, name=item.name)
    db.commit()
    return item


def _own(db: Session, user: User, item_id: uuid.UUID) -> LibraryItem:
    item = db.get(LibraryItem, item_id)
    if item is None or item.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return item


@router.get("/{item_id}/file")
def item_file(item_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = _own(db, user, item_id)
    return _serve(item.storage_key, item.content_type)


@router.delete("/{item_id}", status_code=204)
def delete_item(item_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = _own(db, user, item_id)
    get_storage().delete_prefix(item.storage_key)
    db.delete(item)
    db.commit()

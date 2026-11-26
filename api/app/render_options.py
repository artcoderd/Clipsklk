"""Checks that render options only reference the user's own styles and files."""
import uuid

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.media_presets import PRESET_KEYS
from app.models import LibraryItem, SubtitleStyle
from app.schemas import RenderOptions


def validate_render(db: Session, user_id: uuid.UUID, opts: RenderOptions) -> dict:
    if opts.style_id.startswith("preset:"):
        if opts.style_id.removeprefix("preset:") not in PRESET_KEYS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_style")
    else:
        try:
            row = db.get(SubtitleStyle, uuid.UUID(opts.style_id))
        except ValueError:
            row = None
        if row is None or row.user_id != user_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_style")
    for item_id, kind in ((opts.music_id, "music"), (opts.logo_id, "logo")):
        if item_id is None:
            continue
        item = db.get(LibraryItem, item_id)
        if item is None or item.user_id != user_id or item.kind != kind:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"invalid_{kind}")
    return opts.model_dump(mode="json")

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.media.subtitles import PRESETS
from app.models import SubtitleStyle, User
from app.schemas import StyleConfig, StyleIn, StyleOut

router = APIRouter(prefix="/styles", tags=["styles"])
MAX_STYLES = 30


def _out(row: SubtitleStyle) -> StyleOut:
    return StyleOut(id=str(row.id), name=row.name, preset=False, config=StyleConfig(**row.config))


@router.get("", response_model=list[StyleOut])
def list_styles(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    presets = [StyleOut(id=f"preset:{k}", name=v["name"], preset=True,
                        config=StyleConfig(**{f: v[f] for f in StyleConfig.model_fields}))
               for k, v in PRESETS.items()]
    mine = db.scalars(select(SubtitleStyle).where(SubtitleStyle.user_id == user.id)
                      .order_by(SubtitleStyle.created_at)).all()
    return presets + [_out(r) for r in mine]


@router.post("", response_model=StyleOut, status_code=201)
def create_style(data: StyleIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    count = db.scalar(select(func.count()).select_from(SubtitleStyle).where(SubtitleStyle.user_id == user.id))
    if count >= MAX_STYLES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "too_many_styles")
    row = SubtitleStyle(user_id=user.id, name=data.name.strip(), config=data.config.model_dump())
    db.add(row)
    db.commit()
    return _out(row)


def _own(db: Session, user: User, style_id: uuid.UUID) -> SubtitleStyle:
    row = db.get(SubtitleStyle, style_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return row


@router.put("/{style_id}", response_model=StyleOut)
def update_style(style_id: uuid.UUID, data: StyleIn, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    row = _own(db, user, style_id)
    row.name, row.config = data.name.strip(), data.config.model_dump()
    db.commit()
    return _out(row)


@router.delete("/{style_id}", status_code=204)
def delete_style(style_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.delete(_own(db, user, style_id))
    db.commit()

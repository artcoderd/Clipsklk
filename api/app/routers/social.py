"""Connected accounts and publishing."""
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import activity
from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user
from app.models import Clip, Publication, SocialAccount, User
from app.redis_client import get_redis
from app.routers.clips import _own_clip
from app.schemas import PublicationOut, PublishIn, SocialAccountOut, TelegramIn
from app.social import meta, telegram, tiktok
from app.social.crypto import encrypt
from app.social.http import PlatformError
from app.worker import enqueue_publish

router = APIRouter(tags=["publish"])
PROVIDERS = {"meta": meta, "tiktok": tiktok}


def _redirect_uri(provider: str) -> str:
    return f"{get_settings().web_url.rstrip('/')}/api/social/callback/{provider}"


def save_accounts(db: Session, user: User, found: list[dict]) -> list[SocialAccount]:
    out = []
    for a in found:
        acc = db.scalar(select(SocialAccount).where(SocialAccount.user_id == user.id,
                                                    SocialAccount.platform == a["platform"],
                                                    SocialAccount.external_id == a["external_id"]))
        if acc is None:
            acc = SocialAccount(user_id=user.id, platform=a["platform"], external_id=a["external_id"], token_enc="")
            db.add(acc)
        acc.name, acc.picture, acc.meta = a["name"][:200], a.get("picture"), a.get("meta", {})
        acc.token_enc = encrypt(a["token"])
        acc.refresh_enc = encrypt(a["refresh"]) if a.get("refresh") else None
        acc.expires_at = a.get("expires_at")
        acc.status, acc.last_error = "ok", None
        out.append(acc)
    db.commit()
    return out


@router.get("/social/status")
def social_status(user: User = Depends(get_current_user)):
    return {"meta": meta.configured(), "tiktok": tiktok.configured(), "telegram": True,
            "tiktok_mode": get_settings().tiktok_post_mode}


@router.get("/social/accounts", response_model=list[SocialAccountOut])
def list_accounts(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.scalars(select(SocialAccount).where(SocialAccount.user_id == user.id)
                      .order_by(SocialAccount.platform, SocialAccount.name)).all()


def _own_account(db: Session, user: User, account_id: uuid.UUID) -> SocialAccount:
    acc = db.get(SocialAccount, account_id)
    if acc is None or acc.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return acc


@router.delete("/social/accounts/{account_id}", status_code=204)
def disconnect(account_id: uuid.UUID, request: Request, user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    acc = _own_account(db, user, account_id)
    activity.log(db, "social_disconnected", user.id, request, platform=acc.platform, name=acc.name)
    db.delete(acc)   # its scheduled publications go too (cascade)
    db.commit()


@router.get("/social/connect/{provider}")
def connect(provider: str, user: User = Depends(get_current_user)):
    mod = PROVIDERS.get(provider)
    if mod is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    if not mod.configured():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "provider_not_configured")
    state = secrets.token_urlsafe(24)
    # One-time state tied to this user (protects the callback against CSRF).
    get_redis().set(f"social:state:{state}", json.dumps({"user": str(user.id), "provider": provider}), ex=600)
    return {"url": mod.authorize_url(_redirect_uri(provider), state)}


@router.get("/social/callback/{provider}")
def callback(provider: str, request: Request, code: str | None = None, state: str | None = None,
             error: str | None = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    web = get_settings().web_url.rstrip("/")
    raw = get_redis().getdel(f"social:state:{state}") if state else None
    saved = json.loads(raw) if raw else None
    if not saved or saved["user"] != str(user.id) or saved["provider"] != provider:
        return RedirectResponse(f"{web}/publicar?error=state", status_code=303)
    if error or not code:
        return RedirectResponse(f"{web}/publicar?error=denied", status_code=303)
    try:
        found = PROVIDERS[provider].connect(code, _redirect_uri(provider))
    except PlatformError as e:
        return RedirectResponse(f"{web}/publicar?error={e.code}", status_code=303)
    accounts = save_accounts(db, user, found)
    activity.log(db, "social_connected", user.id, request, provider=provider, count=len(accounts))
    db.commit()
    return RedirectResponse(f"{web}/publicar?connected={provider}&n={len(accounts)}", status_code=303)


@router.post("/social/telegram", response_model=SocialAccountOut, status_code=201)
def connect_telegram(data: TelegramIn, request: Request, user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    try:
        found = telegram.connect(data.bot_token, data.chat_id.strip())
    except PlatformError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, e.code if e.code.startswith("telegram") else "telegram_failed")
    acc = save_accounts(db, user, [found])[0]
    activity.log(db, "social_connected", user.id, request, provider="telegram")
    db.commit()
    return acc


@router.get("/social/accounts/{account_id}/tiktok")
def tiktok_creator(account_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """What this TikTok account allows (privacy options, nickname), shown before posting."""
    from app.social.tokens import access_token

    acc = _own_account(db, user, account_id)
    if acc.platform != "tiktok":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "not_tiktok")
    try:
        info = tiktok.creator_info(access_token(db, acc))
    except PlatformError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, e.code)
    return {"nickname": info.get("creator_nickname"), "privacy_options": info.get("privacy_level_options", []),
            "max_duration": info.get("max_video_post_duration_sec"),
            "comment_disabled": info.get("comment_disabled", False), "duet_disabled": info.get("duet_disabled", False),
            "stitch_disabled": info.get("stitch_disabled", False), "mode": get_settings().tiktok_post_mode}


@router.post("/clips/{clip_id}/publish", response_model=list[PublicationOut], status_code=201)
def publish(clip_id: uuid.UUID, data: PublishIn, request: Request, user: User = Depends(get_current_user),
            db: Session = Depends(get_db)):
    clip = _own_clip(db, user, clip_id)
    if clip.render_status != "ready":
        raise HTTPException(status.HTTP_409_CONFLICT, "clip_busy")
    now = datetime.now(timezone.utc)
    when = data.scheduled_at
    if when is not None:
        when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
        if when < now - timedelta(minutes=1):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "schedule_in_past")
        if when > now + timedelta(days=75):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "schedule_too_far")
    pubs = []
    for aid in dict.fromkeys(data.account_ids):
        acc = _own_account(db, user, aid)
        if acc.platform == "tiktok" and get_settings().tiktok_post_mode == "direct" \
                and not data.options.get("privacy_level"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "tiktok_privacy_required")
        pub = Publication(user_id=user.id, clip_id=clip.id, account_id=acc.id, caption=data.caption,
                          options=data.options, scheduled_at=when or now,
                          status="scheduled" if when else "publishing")
        db.add(pub)
        pubs.append(pub)
    activity.log(db, "publish_requested", user.id, request, clip=str(clip.id), accounts=len(pubs),
                 scheduled=when.isoformat() if when else None)
    db.commit()
    for pub in pubs:
        if pub.status == "publishing":
            enqueue_publish(pub.id)
    return pubs


@router.get("/clips/{clip_id}/publications", response_model=list[PublicationOut])
def clip_publications(clip_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _own_clip(db, user, clip_id)
    return db.scalars(select(Publication).where(Publication.clip_id == clip_id)
                      .order_by(Publication.scheduled_at.desc())).all()


@router.get("/publications", response_model=list[PublicationOut])
def list_publications(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.scalars(select(Publication).where(Publication.user_id == user.id)
                      .order_by(Publication.scheduled_at.desc()).limit(200)).all()


def _own_pub(db: Session, user: User, pub_id: uuid.UUID) -> Publication:
    pub = db.get(Publication, pub_id)
    if pub is None or pub.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return pub


@router.delete("/publications/{pub_id}", response_model=PublicationOut)
def cancel(pub_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    pub = _own_pub(db, user, pub_id)
    if pub.status != "scheduled":
        raise HTTPException(status.HTTP_409_CONFLICT, "not_cancellable")
    pub.status = "canceled"
    db.commit()
    return pub


@router.post("/publications/{pub_id}/retry", response_model=PublicationOut)
def retry(pub_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    pub = _own_pub(db, user, pub_id)
    if pub.status != "failed":
        raise HTTPException(status.HTTP_409_CONFLICT, "not_retryable")
    pub.status, pub.error = "publishing", None
    db.commit()
    enqueue_publish(pub.id)
    return pub

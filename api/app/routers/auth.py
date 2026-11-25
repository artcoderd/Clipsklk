from datetime import datetime, timezone

import segno
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import activity, ratelimit
from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user
from app.models import Invitation, Role, User
from app.schemas import (InvitationLookupOut, LoginIn, RegisterIn, TotpCodeIn,
                         TotpSetupOut, UserOut)
from app.security import (DUMMY_HASH, create_session_token, hash_password, hash_token,
                          new_totp_secret, totp_uri, verify_password, verify_totp)

router = APIRouter(prefix="/auth", tags=["auth"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _set_session(response: Response, user: User) -> None:
    s = get_settings()
    response.set_cookie(
        s.session_cookie_name,
        create_session_token(str(user.id)),
        max_age=s.session_ttl_hours * 3600,
        httponly=True,
        secure=s.is_production,
        samesite="lax",
        path="/",
    )


def _valid_invitation(db: Session, token: str) -> Invitation | None:
    inv = db.scalar(select(Invitation).where(Invitation.token_hash == hash_token(token)))
    if inv is None or inv.used_at or inv.revoked_at or _aware(inv.expires_at) < _now():
        return None
    return inv


@router.get("/config")
def public_config():
    return {"public_signup": get_settings().public_signup}


@router.get("/invitations/{token}", response_model=InvitationLookupOut)
def lookup_invitation(token: str, db: Session = Depends(get_db)):
    inv = _valid_invitation(db, token)
    if inv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "invitation_invalid")
    return InvitationLookupOut(email=inv.email, expires_at=inv.expires_at)


@router.post("/register", response_model=UserOut, status_code=201)
def register(data: RegisterIn, request: Request, response: Response, db: Session = Depends(get_db)):
    email = data.email.lower()
    role = Role.user
    inv = None
    if data.invitation_token:
        inv = _valid_invitation(db, data.invitation_token)
        if inv is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "invitation_invalid")
        if inv.email.lower() != email:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "invitation_email_mismatch")
        role = inv.role if inv.role != Role.owner else Role.user
    elif not get_settings().public_signup:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "invitation_required")

    if db.scalar(select(func.count()).select_from(User).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "email_taken")

    user = User(email=email, name=data.name.strip(), password_hash=hash_password(data.password), role=role)
    db.add(user)
    if inv is not None:
        inv.used_at = _now()
    db.flush()
    activity.log(db, "register", user.id, request, via="invitation" if inv else "public")
    db.commit()
    _set_session(response, user)
    return user


@router.post("/login", response_model=UserOut)
def login(data: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    email = data.email.lower()
    ip = activity.client_ip(request) or "unknown"
    if ratelimit.is_blocked("login-email", email) or ratelimit.is_blocked("login-ip", ip):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too_many_attempts")

    user = db.scalar(select(User).where(User.email == email))
    ok = verify_password(data.password, user.password_hash if user else DUMMY_HASH)
    if not ok or user is None or not user.is_active:
        ratelimit.register_failure("login-email", email)
        ratelimit.register_failure("login-ip", ip)
        activity.log(db, "login_failed", user.id if user else None, request, email=email)
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_credentials")

    if user.totp_enabled:
        if not data.totp_code:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "totp_required")
        if not verify_totp(user.totp_secret, data.totp_code):
            ratelimit.register_failure("login-email", email)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "totp_invalid")

    ratelimit.reset("login-email", email)
    user.last_login_at = _now()
    activity.log(db, "login", user.id, request)
    db.commit()
    _set_session(response, user)
    return user


@router.post("/logout", status_code=204)
def logout(response: Response):
    response.delete_cookie(get_settings().session_cookie_name, path="/")


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


@router.post("/2fa/setup", response_model=TotpSetupOut)
def totp_setup(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.totp_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "2fa_already_enabled")
    user.totp_secret = new_totp_secret()
    db.commit()
    uri = totp_uri(user.totp_secret, user.email)
    qr = segno.make(uri, error="m").svg_data_uri(scale=6, border=2, dark="#000000", light="#ffffff")
    return TotpSetupOut(secret=user.totp_secret, otpauth_uri=uri, qr_data_uri=qr)


@router.post("/2fa/enable", response_model=UserOut)
def totp_enable(data: TotpCodeIn, request: Request, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    if user.totp_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "2fa_already_enabled")
    if not verify_totp(user.totp_secret, data.code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "totp_invalid")
    user.totp_enabled = True
    activity.log(db, "2fa_enabled", user.id, request)
    db.commit()
    return user

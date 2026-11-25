import uuid

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import Role, User
from app.security import decode_session_token


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(get_settings().session_cookie_name)
    sub = decode_session_token(token) if token else None
    if not sub:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not_authenticated")
    try:
        user = db.get(User, uuid.UUID(sub))
    except ValueError:
        user = None
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not_authenticated")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    # 2FA is mandatory for anyone with admin powers.
    if not user.totp_enabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "2fa_required")
    return user


def require_owner(user: User = Depends(require_admin)) -> User:
    if user.role != Role.owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "owner_only")
    return user

import uuid

from fastapi import Request
from sqlalchemy.orm import Session

from app.models import ActivityLog


def client_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    fwd = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


def log(db: Session, action: str, user_id: uuid.UUID | None = None,
        request: Request | None = None, **detail) -> None:
    db.add(ActivityLog(user_id=user_id, action=action, detail=detail, ip=client_ip(request)))

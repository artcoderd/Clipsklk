from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import push
from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user
from app.models import PushSubscription, User
from app.schemas import PushSubscriptionIn

router = APIRouter(prefix="/push", tags=["push"])


@router.get("/key")
def public_key():
    """VAPID public key the browser needs to subscribe (null if push isn't configured)."""
    return {"key": get_settings().vapid_public_key if push.enabled() else None}


@router.post("/subscriptions", status_code=201)
def subscribe(data: PushSubscriptionIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if get_settings().is_production and not data.endpoint.startswith("https://"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_subscription")
    if not data.keys.get("p256dh") or not data.keys.get("auth"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_subscription")
    sub = db.scalar(select(PushSubscription).where(PushSubscription.endpoint == data.endpoint))
    if sub is None:
        sub = PushSubscription(endpoint=data.endpoint, user_id=user.id, p256dh="", auth="")
        db.add(sub)
    sub.user_id, sub.p256dh, sub.auth = user.id, data.keys["p256dh"], data.keys["auth"]
    db.commit()
    return {"ok": True}


@router.delete("/subscriptions", status_code=204)
def unsubscribe(data: PushSubscriptionIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    sub = db.scalar(select(PushSubscription).where(PushSubscription.endpoint == data.endpoint,
                                                   PushSubscription.user_id == user.id))
    if sub:
        db.delete(sub)
        db.commit()


@router.post("/test")
def test_push(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    sent = push.send_to_user(db, user.id, "Clipeo", "Las notificaciones funcionan ✅", "/", "test")
    return {"sent": sent}

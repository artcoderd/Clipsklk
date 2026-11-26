"""Web Push notifications (works on Android/desktop browsers, and on iPhone
once Clipeo is added to the home screen, iOS 16.4+)."""
import json
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import PushSubscription

log = logging.getLogger(__name__)


def enabled() -> bool:
    s = get_settings()
    return bool(s.vapid_public_key and s.vapid_private_key)


def send_to_user(db: Session, user_id: uuid.UUID, title: str, body: str, url: str = "/", tag: str | None = None) -> int:
    """Sends to every device of the user; drops subscriptions the push service
    says are gone. Returns how many were delivered."""
    if not enabled():
        return 0
    from pywebpush import WebPushException, webpush

    s = get_settings()
    payload = json.dumps({"title": title, "body": body, "url": url, "tag": tag})
    sent = 0
    for sub in db.scalars(select(PushSubscription).where(PushSubscription.user_id == user_id)).all():
        try:
            webpush({"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}}, payload,
                    vapid_private_key=s.vapid_private_key, vapid_claims={"sub": s.vapid_subject}, ttl=3600,
                    timeout=10)
            sent += 1
        except WebPushException as e:
            code = getattr(e.response, "status_code", None)
            if code in (404, 410):     # unsubscribed / expired
                db.delete(sub)
            else:
                log.warning("push failed (%s): %s", code, e)
        except Exception as e:
            log.warning("push failed: %s", e)
    db.commit()
    return sent

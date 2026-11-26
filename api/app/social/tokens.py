"""A usable access token for an account (refreshing TikTok's when due)."""
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models import SocialAccount
from app.social import tiktok
from app.social.crypto import decrypt, encrypt
from app.social.http import PlatformError


def access_token(db: Session, acc: SocialAccount) -> str:
    token = decrypt(acc.token_enc)
    if not token:
        raise PlatformError("account_expired", "token unreadable")
    exp = acc.expires_at
    if acc.platform == "tiktok" and exp is not None:
        exp = exp if exp.tzinfo else exp.replace(tzinfo=timezone.utc)
        if exp - timedelta(minutes=5) < datetime.now(timezone.utc):
            refresh = decrypt(acc.refresh_enc)
            if not refresh:
                raise PlatformError("account_expired", "no refresh token")
            t = tiktok.refresh(refresh)
            acc.token_enc, acc.expires_at = encrypt(t["token"]), t["expires_at"]
            if t.get("refresh"):
                acc.refresh_enc = encrypt(t["refresh"])
            db.commit()
            token = t["token"]
    return token

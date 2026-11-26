"""Telegram: the user's own bot sends the clip to a chat/channel/group."""
import re
from pathlib import Path

from app.config import get_settings
from app.social.http import PlatformError, check, client
TOKEN_RE = re.compile(r"^\d{5,12}:[A-Za-z0-9_-]{30,}$")
MAX_BYTES = 50 * 1024 * 1024     # bot upload limit on api.telegram.org


def _api() -> str:
    return get_settings().telegram_api_base.rstrip("/")


def connect(bot_token: str, chat_id: str) -> dict:
    if not TOKEN_RE.match(bot_token.strip()):
        raise PlatformError("telegram_bad_token")
    with client() as c:
        bot = check(c.get(f"{_api()}/bot{bot_token}/getMe"), "telegram_bot")["result"]
        chat = check(c.get(f"{_api()}/bot{bot_token}/getChat", params={"chat_id": chat_id}), "telegram_chat")["result"]
    name = chat.get("title") or chat.get("username") or " ".join(
        x for x in (chat.get("first_name"), chat.get("last_name")) if x) or str(chat_id)
    return {"platform": "telegram", "external_id": f"{bot['id']}:{chat['id']}", "name": f"{name} · @{bot['username']}",
            "token": bot_token.strip(), "meta": {"chat_id": str(chat["id"])}, "picture": None}


def publish(token: str, chat_id: str, video: Path, caption: str) -> dict:
    if video.stat().st_size > MAX_BYTES and "api.telegram.org" in _api():
        raise PlatformError("telegram_too_large")
    with client(timeout=600) as c, open(video, "rb") as f:
        msg = check(c.post(f"{_api()}/bot{token}/sendVideo",
                           data={"chat_id": chat_id, "caption": caption[:1024], "supports_streaming": "true"},
                           files={"video": (video.name, f, "video/mp4")}), "telegram_send")["result"]
    chat = msg.get("chat", {})
    url = f"https://t.me/{chat['username']}/{msg['message_id']}" if chat.get("username") else None
    return {"id": str(msg["message_id"]), "url": url}

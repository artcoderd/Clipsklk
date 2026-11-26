"""TikTok (Login Kit + Content Posting API v2).

Two ways to post, set with TIKTOK_POST_MODE:
- inbox: the video lands in the creator's TikTok inbox/drafts and they finish
  the post in the app (works before TikTok audits the app).
- direct: posted right away with the privacy the user chose (needs the
  video.publish scope; unaudited apps can only post privately).
TikTok's guidelines require showing the creator's nickname and letting them
pick the privacy level from the options their account allows (creator_info).
"""
import math
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

from app.config import get_settings
from app.social.http import PlatformError, check, client

API = "https://open.tiktokapis.com/v2"
SCOPES = ["user.info.basic", "video.upload", "video.publish"]
CHUNK = 10 * 1024 * 1024      # TikTok accepts 5-64 MB chunks (smaller files go in one)


def configured() -> bool:
    s = get_settings()
    return bool(s.tiktok_client_key and s.tiktok_client_secret)


def authorize_url(redirect_uri: str, state: str) -> str:
    q = urlencode({"client_key": get_settings().tiktok_client_key, "scope": ",".join(SCOPES),
                   "response_type": "code", "redirect_uri": redirect_uri, "state": state})
    return f"https://www.tiktok.com/v2/auth/authorize/?{q}"


def _tokens(body: dict) -> dict:
    now = datetime.now(timezone.utc)
    return {"token": body["access_token"], "refresh": body.get("refresh_token"),
            "expires_at": now + timedelta(seconds=int(body.get("expires_in", 86400)) - 60),
            "open_id": body.get("open_id")}


def connect(code: str, redirect_uri: str) -> list[dict]:
    s = get_settings()
    with client() as c:
        t = _tokens(check(c.post(f"{API}/oauth/token/", data={
            "client_key": s.tiktok_client_key, "client_secret": s.tiktok_client_secret, "code": code,
            "grant_type": "authorization_code", "redirect_uri": redirect_uri}), "tiktok_oauth"))
        user = check(c.get(f"{API}/user/info/", params={"fields": "open_id,display_name,avatar_url"},
                           headers={"Authorization": f"Bearer {t['token']}"}), "tiktok_user")["data"]["user"]
    return [{"platform": "tiktok", "external_id": user.get("open_id") or t["open_id"],
             "name": user.get("display_name") or "TikTok", "picture": user.get("avatar_url"),
             "token": t["token"], "refresh": t["refresh"], "expires_at": t["expires_at"], "meta": {}}]


def refresh(refresh_token: str) -> dict:
    s = get_settings()
    with client() as c:
        return _tokens(check(c.post(f"{API}/oauth/token/", data={
            "client_key": s.tiktok_client_key, "client_secret": s.tiktok_client_secret,
            "grant_type": "refresh_token", "refresh_token": refresh_token}), "tiktok_refresh"))


def creator_info(token: str) -> dict:
    with client() as c:
        return check(c.post(f"{API}/post/publish/creator_info/query/", json={},
                            headers={"Authorization": f"Bearer {token}"}), "tiktok_creator")["data"]


def publish(token: str, video: Path, caption: str, options: dict, wait_s: float = 300, poll_s: float = 5) -> dict:
    mode = get_settings().tiktok_post_mode
    size = video.stat().st_size
    chunk = size if size <= 64 * 1024 * 1024 else CHUNK
    count = max(1, math.floor(size / chunk))       # the last chunk absorbs the remainder
    source = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": count}
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"}
    with client(timeout=600) as c:
        if mode == "direct":
            privacy = options.get("privacy_level")
            if not privacy:
                raise PlatformError("tiktok_privacy_required")
            body = {"post_info": {"title": caption[:2200], "privacy_level": privacy,
                                  "disable_comment": not options.get("allow_comment", True),
                                  "disable_duet": not options.get("allow_duet", True),
                                  "disable_stitch": not options.get("allow_stitch", True)},
                    "source_info": source}
            init = check(c.post(f"{API}/post/publish/video/init/", json=body, headers=headers), "tiktok_init")["data"]
        else:
            init = check(c.post(f"{API}/post/publish/inbox/video/init/", json={"source_info": source},
                                headers=headers), "tiktok_init")["data"]
        with open(video, "rb") as f:
            for i in range(count):
                start = i * chunk
                end = size - 1 if i == count - 1 else start + chunk - 1
                f.seek(start)
                data = f.read(end - start + 1)
                r = c.put(init["upload_url"], content=data, headers={
                    "Content-Type": "video/mp4", "Content-Length": str(len(data)),
                    "Content-Range": f"bytes {start}-{end}/{size}"})
                if r.status_code not in (200, 201, 206):
                    raise PlatformError("tiktok_upload_failed", r.text[:300], retry=r.status_code >= 500)
        publish_id = init["publish_id"]
        deadline = time.time() + wait_s
        while True:
            st = check(c.post(f"{API}/post/publish/status/fetch/", json={"publish_id": publish_id},
                              headers=headers), "tiktok_status")["data"]
            status = st.get("status")
            if status in ("PUBLISH_COMPLETE", "SEND_TO_USER_INBOX"):
                ids = st.get("publicaly_available_post_id") or st.get("publicly_available_post_id") or []
                return {"id": str(ids[0]) if ids else publish_id, "draft": status == "SEND_TO_USER_INBOX",
                        "url": None}
            if status == "FAILED":
                raise PlatformError("tiktok_publish_failed", str(st.get("fail_reason", "")))
            if time.time() > deadline:
                raise PlatformError("tiktok_processing_timeout", status or "", retry=True)
            time.sleep(poll_s)

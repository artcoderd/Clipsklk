"""Facebook Pages + Instagram professional accounts (Meta Graph API).

Connect: Facebook Login (OAuth) -> long-lived user token -> the user's Pages
(each with its own page token) and the Instagram account linked to each Page.
Publish Reels by uploading the file to Meta (no public URL needed):
  Facebook: /{page}/video_reels start -> rupload binary -> finish (PUBLISHED)
  Instagram: /{ig}/media (REELS, resumable) -> rupload binary -> wait FINISHED -> media_publish
Requires an app reviewed by Meta for the permissions below (until then, only
the app's own admins/testers can connect).
"""
import time
from pathlib import Path
from urllib.parse import urlencode

from app.config import get_settings
from app.social.http import PlatformError, check, client

SCOPES = ["pages_show_list", "pages_read_engagement", "pages_manage_posts", "business_management",
          "instagram_basic", "instagram_content_publish"]


def _graph() -> str:
    return f"https://graph.facebook.com/{get_settings().meta_graph_version}"


def configured() -> bool:
    s = get_settings()
    return bool(s.meta_app_id and s.meta_app_secret)


def authorize_url(redirect_uri: str, state: str) -> str:
    s = get_settings()
    q = urlencode({"client_id": s.meta_app_id, "redirect_uri": redirect_uri, "state": state,
                   "scope": ",".join(SCOPES), "response_type": "code"})
    return f"https://www.facebook.com/{s.meta_graph_version}/dialog/oauth?{q}"


def connect(code: str, redirect_uri: str) -> list[dict]:
    """Code -> the Pages and Instagram accounts the user granted, with long-lived tokens."""
    s = get_settings()
    with client() as c:
        short = check(c.get(f"{_graph()}/oauth/access_token", params={
            "client_id": s.meta_app_id, "client_secret": s.meta_app_secret,
            "redirect_uri": redirect_uri, "code": code}), "meta_oauth")["access_token"]
        long_user = check(c.get(f"{_graph()}/oauth/access_token", params={
            "grant_type": "fb_exchange_token", "client_id": s.meta_app_id, "client_secret": s.meta_app_secret,
            "fb_exchange_token": short}), "meta_oauth")["access_token"]
        # Page tokens obtained with a long-lived user token don't expire.
        pages = check(c.get(f"{_graph()}/me/accounts", params={
            "access_token": long_user,
            "fields": "id,name,access_token,picture{url},instagram_business_account{id,username,profile_picture_url}"}),
            "meta_pages").get("data", [])
    out = []
    for p in pages:
        out.append({"platform": "facebook", "external_id": p["id"], "name": p["name"], "token": p["access_token"],
                    "picture": ((p.get("picture") or {}).get("data") or {}).get("url"), "meta": {"page_id": p["id"]}})
        ig = p.get("instagram_business_account")
        if ig:
            out.append({"platform": "instagram", "external_id": ig["id"], "name": "@" + ig.get("username", ig["id"]),
                        "token": p["access_token"], "picture": ig.get("profile_picture_url"),
                        "meta": {"page_id": p["id"], "ig_id": ig["id"]}})
    if not out:
        raise PlatformError("meta_no_pages", "the user granted no Pages")
    return out


def _rupload(c, url: str, token: str, video: Path) -> None:
    size = video.stat().st_size
    with open(video, "rb") as f:
        check(c.post(url, headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size),
                                   "Content-Type": "application/octet-stream"}, content=f.read()), "meta_upload")


def publish_facebook(token: str, page_id: str, video: Path, caption: str) -> dict:
    with client(timeout=600) as c:
        start = check(c.post(f"{_graph()}/{page_id}/video_reels",
                             params={"upload_phase": "start", "access_token": token}), "facebook_start")
        video_id = start["video_id"]
        _rupload(c, start.get("upload_url") or f"https://rupload.facebook.com/video-upload/"
                 f"{get_settings().meta_graph_version}/{video_id}", token, video)
        check(c.post(f"{_graph()}/{page_id}/video_reels", params={
            "upload_phase": "finish", "video_id": video_id, "video_state": "PUBLISHED",
            "description": caption, "access_token": token}), "facebook_finish")
    return {"id": video_id, "url": f"https://www.facebook.com/reel/{video_id}"}


def publish_instagram(token: str, ig_id: str, video: Path, caption: str, cover_ms: int | None = None,
                      wait_s: float = 300, poll_s: float = 5) -> dict:
    with client(timeout=600) as c:
        params = {"media_type": "REELS", "upload_type": "resumable", "caption": caption,
                  "share_to_feed": "true", "access_token": token}
        if cover_ms is not None:
            params["thumb_offset"] = str(cover_ms)
        container = check(c.post(f"{_graph()}/{ig_id}/media", params=params), "instagram_container")
        cid = container["id"]
        _rupload(c, container.get("uri") or f"https://rupload.facebook.com/ig-api-upload/"
                 f"{get_settings().meta_graph_version}/{cid}", token, video)
        # Instagram processes the video before it can be published.
        deadline = time.time() + wait_s
        while True:
            st = check(c.get(f"{_graph()}/{cid}", params={"fields": "status_code,status", "access_token": token}),
                       "instagram_status")
            code = st.get("status_code")
            if code == "FINISHED":
                break
            if code in ("ERROR", "EXPIRED"):
                raise PlatformError("instagram_processing_failed", str(st.get("status", code)))
            if time.time() > deadline:
                raise PlatformError("instagram_processing_timeout", "still processing", retry=True)
            time.sleep(poll_s)
        media = check(c.post(f"{_graph()}/{ig_id}/media_publish", params={"creation_id": cid, "access_token": token}),
                      "instagram_publish")
        link = check(c.get(f"{_graph()}/{media['id']}", params={"fields": "permalink", "access_token": token}),
                     "instagram_permalink").get("permalink")
    return {"id": media["id"], "url": link}

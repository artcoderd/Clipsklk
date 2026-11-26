"""Fake Meta Graph / rupload, TikTok and Telegram APIs (by path), recording requests."""
import json
from urllib.parse import parse_qs

import httpx2


class FakePlatforms:
    def __init__(self):
        self.calls: list[tuple[str, str, dict, bytes]] = []
        self.ig_polls = 0
        self.fail: dict[str, int] = {}          # path fragment -> HTTP status to return
        self.tiktok_status = "SEND_TO_USER_INBOX"

    def _json(self, data, status=200):
        return httpx2.Response(status, content=json.dumps(data).encode(), headers={"content-type": "application/json"})

    def handler(self, req: httpx2.Request) -> httpx2.Response:
        url = str(req.url)
        path = req.url.path
        q = {k: v[0] for k, v in parse_qs(req.url.query.decode() if isinstance(req.url.query, bytes) else str(req.url.query)).items()}
        body = req.content or b""
        self.calls.append((req.method, url, dict(req.headers), body))
        for frag, st in self.fail.items():
            if frag in url:
                return self._json({"error": {"message": f"fail {st}", "code": 190 if st == 401 else 2}}, st)
        host = req.url.host
        if host == "graph.facebook.com":
            if path.endswith("/oauth/access_token"):
                return self._json({"access_token": "LONG" if q.get("grant_type") == "fb_exchange_token" else "SHORT"})
            if path.endswith("/me/accounts"):
                return self._json({"data": [{"id": "PAGE1", "name": "Mi Página", "access_token": "PAGETOKEN",
                                             "instagram_business_account": {"id": "IG1", "username": "micanal"}},
                                            {"id": "PAGE2", "name": "Otra", "access_token": "PAGETOKEN2"}]})
            if path.endswith("/video_reels"):
                if q.get("upload_phase") == "start":
                    return self._json({"video_id": "VID1", "upload_url": "https://rupload.facebook.com/video-upload/v23.0/VID1"})
                return self._json({"success": True})
            if path.endswith("/IG1/media"):
                return self._json({"id": "CONT1", "uri": "https://rupload.facebook.com/ig-api-upload/v23.0/CONT1"})
            if path.endswith("/CONT1"):
                self.ig_polls += 1
                return self._json({"status_code": "FINISHED" if self.ig_polls >= 2 else "IN_PROGRESS"})
            if path.endswith("/media_publish"):
                return self._json({"id": "IGMEDIA1"})
            if path.endswith("/IGMEDIA1"):
                return self._json({"permalink": "https://www.instagram.com/reel/abc/"})
        if host == "rupload.facebook.com":
            return self._json({"success": True})
        if host == "open.tiktokapis.com":
            if path.endswith("/oauth/token/"):
                form = parse_qs(body.decode())
                tok = "TT_REFRESHED" if form.get("grant_type") == ["refresh_token"] else "TT_ACCESS"
                return self._json({"access_token": tok, "refresh_token": "TT_REFRESH", "expires_in": 86400,
                                   "open_id": "OPEN1"})
            if path.endswith("/user/info/"):
                return self._json({"data": {"user": {"open_id": "OPEN1", "display_name": "Creador"}}})
            if path.endswith("/creator_info/query/"):
                return self._json({"data": {"creator_nickname": "creador", "privacy_level_options":
                                            ["PUBLIC_TO_EVERYONE", "SELF_ONLY"], "max_video_post_duration_sec": 600},
                                   "error": {"code": "ok"}})
            if path.endswith("/video/init/"):
                return self._json({"data": {"publish_id": "PUB1", "upload_url": "https://open-upload.tiktokapis.com/upload/?id=1"},
                                   "error": {"code": "ok"}})
            if path.endswith("/status/fetch/"):
                return self._json({"data": {"status": self.tiktok_status, "publicaly_available_post_id": [7300000001]},
                                   "error": {"code": "ok"}})
        if host == "open-upload.tiktokapis.com":
            return httpx2.Response(201)
        if host == "api.telegram.org":
            if path.endswith("/getMe"):
                return self._json({"ok": True, "result": {"id": 999, "username": "clipeo_bot"}})
            if path.endswith("/getChat"):
                return self._json({"ok": True, "result": {"id": -100123, "title": "Mi canal", "username": "micanalpublico"}})
            if path.endswith("/sendVideo"):
                return self._json({"ok": True, "result": {"message_id": 42, "chat": {"id": -100123, "username": "micanalpublico"}}})
        return self._json({"error": {"message": f"unexpected {url}"}}, 404)

    def find(self, frag: str) -> list:
        return [c for c in self.calls if frag in c[1]]

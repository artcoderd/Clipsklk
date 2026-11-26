import shutil
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx2
import pytest

from app.config import get_settings
from app.db import SessionLocal
from app.models import Publication, SocialAccount
from app.social import http as social_http
from app.worker import tasks
from tests.conftest import login, make_user
from tests.media_fixtures import fake_transcript, make_video
from tests.social_mocks import FakePlatforms

TG_TOKEN = "123456789:AAEhBOweik6ad9r_QXMENQjcrGbqCr4K-ra"


@pytest.fixture(scope="module")
def video(tmp_path_factory):
    return make_video(tmp_path_factory.mktemp("so") / "talk.mp4")


@pytest.fixture
def fake(monkeypatch):
    f = FakePlatforms()
    monkeypatch.setattr(social_http, "TRANSPORT", httpx2.MockTransport(f.handler))
    s = get_settings()
    for k, v in (("meta_app_id", "APPID"), ("meta_app_secret", "APPSECRET"), ("tiktok_client_key", "TTKEY"),
                 ("tiktok_client_secret", "TTSECRET"), ("tiktok_post_mode", "inbox")):
        monkeypatch.setattr(s, k, v)
    monkeypatch.setattr("app.social.meta.time.sleep", lambda s: None)
    monkeypatch.setattr("app.social.tiktok.time.sleep", lambda s: None)
    return f


@pytest.fixture
def clip(client, monkeypatch, video):
    def fake_download(url, dest, on_progress=None, max_bytes=None):
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy(video, dest / "source.mp4")
        return dest / "source.mp4", {"title": "Charla", "duration": 60}

    monkeypatch.setattr(tasks, "download", fake_download)
    monkeypatch.setattr(tasks, "transcribe", lambda a, d, on_progress=None, language=None: fake_transcript(d))
    monkeypatch.setattr(tasks, "analyze_video", lambda *a, **k: {"fps": 4, "size": [640, 360], "samples": [],
                                                                  "track_person": {}, "people": []})
    queued = []
    monkeypatch.setattr("app.routers.social.enqueue_publish", lambda pid: queued.append(str(pid)))
    monkeypatch.setattr("app.worker.enqueue_publish", lambda pid: queued.append(str(pid)))
    make_user("u@x.com")
    login(client, "u@x.com")
    job = client.post("/jobs", json={"url": "https://youtu.be/pub1", "clip_count": 1, "clip_duration": 20}).json()
    tasks.process_job.run(job["id"])
    return client.get(f"/jobs/{job['id']}/clips").json()[0], queued


def oauth(client, provider):
    url = client.get(f"/social/connect/{provider}").json()["url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    return url, state


def test_meta_and_tiktok_connect(client, fake, clip):
    url, state = oauth(client, "meta")
    assert url.startswith("https://www.facebook.com/v23.0/dialog/oauth") and "instagram_content_publish" in url
    r = client.get(f"/social/callback/meta?code=CODE&state={state}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("/publicar?connected=meta&n=3")
    # The state is single-use.
    r = client.get(f"/social/callback/meta?code=CODE&state={state}", follow_redirects=False)
    assert r.headers["location"].endswith("/publicar?error=state")
    accounts = client.get("/social/accounts").json()
    assert sorted((a["platform"], a["name"]) for a in accounts) == [
        ("facebook", "Mi Página"), ("facebook", "Otra"), ("instagram", "@micanal")]
    db = SessionLocal()
    stored = db.query(SocialAccount).filter_by(platform="instagram").one()
    assert "PAGETOKEN" not in stored.token_enc                # encrypted at rest
    db.close()

    _, state = oauth(client, "tiktok")
    r = client.get(f"/social/callback/tiktok?code=C2&state={state}", follow_redirects=False)
    assert r.headers["location"].endswith("connected=tiktok&n=1")
    tt = next(a for a in client.get("/social/accounts").json() if a["platform"] == "tiktok")
    info = client.get(f"/social/accounts/{tt['id']}/tiktok").json()
    assert info["privacy_options"] == ["PUBLIC_TO_EVERYONE", "SELF_ONLY"] and info["nickname"] == "creador"

    # Another user's state can't be used.
    _, state = oauth(client, "meta")
    make_user("v@x.com")
    client.cookies.clear()
    login(client, "v@x.com")
    r = client.get(f"/social/callback/meta?code=CODE&state={state}", follow_redirects=False)
    assert r.headers["location"].endswith("error=state") and client.get("/social/accounts").json() == []


def test_publish_everywhere(client, fake, clip, video):
    clip, queued = clip
    _, state = oauth(client, "meta")
    client.get(f"/social/callback/meta?code=C&state={state}", follow_redirects=False)
    _, state = oauth(client, "tiktok")
    client.get(f"/social/callback/tiktok?code=C&state={state}", follow_redirects=False)
    assert client.post("/social/telegram", json={"bot_token": "esto-no-es-un-token", "chat_id": "1"}).json()["detail"] == "telegram_bad_token"
    tg = client.post("/social/telegram", json={"bot_token": TG_TOKEN, "chat_id": "@micanal"}).json()
    assert tg["name"] == "Mi canal · @clipeo_bot"
    accs = {a["platform"] + ":" + a["name"]: a["id"] for a in client.get("/social/accounts").json()}
    ids = [accs["facebook:Mi Página"], accs["instagram:@micanal"], accs["tiktok:Creador"], tg["id"]]

    pubs = client.post(f"/clips/{clip['id']}/publish", json={"account_ids": ids, "caption": "Mira esto #humor"}).json()
    assert len(pubs) == 4 and all(p["status"] == "publishing" for p in pubs) and len(queued) == 4
    for pid in queued:
        tasks.publish_post.run(pid)
    result = {p["account_id"]: p for p in client.get(f"/clips/{clip['id']}/publications").json()}
    assert result[accs["facebook:Mi Página"]]["status"] == "published"
    assert result[accs["instagram:@micanal"]]["external_url"] == "https://www.instagram.com/reel/abc/"
    assert result[accs["tiktok:Creador"]]["status"] == "draft_sent"            # inbox mode
    assert result[tg["id"]]["external_url"] == "https://t.me/micanalpublico/42"

    from app.storage import get_storage
    db = SessionLocal()
    from app.models import Clip
    size = get_storage().local_path(db.get(Clip, uuid.UUID(clip["id"])).storage_key).stat().st_size
    db.close()
    # Facebook: start -> binary upload of the whole file with OAuth header -> finish with the caption.
    fb_up = fake.find("rupload.facebook.com/video-upload")[0]
    assert fb_up[2]["authorization"] == "OAuth PAGETOKEN" and fb_up[2]["file_size"] == str(size) and len(fb_up[3]) == size
    assert "description=Mira+esto" in fake.find("upload_phase=finish")[0][1]
    # Instagram: resumable container, waited until FINISHED, then published.
    assert "media_type=REELS" in fake.find("/IG1/media")[0][1] and "upload_type=resumable" in fake.find("/IG1/media")[0][1]
    assert fake.ig_polls >= 2 and fake.find("media_publish")
    # TikTok: inbox init with the exact size, one chunk with Content-Range.
    init = fake.find("/inbox/video/init/")[0]
    assert f'"video_size":{size}' in init[3].decode().replace(" ", "")
    put = fake.find("open-upload.tiktokapis.com")[0]
    assert put[2]["content-range"] == f"bytes 0-{size - 1}/{size}"
    # Telegram: multipart with the caption.
    assert b"Mira esto #humor" in fake.find("/sendVideo")[0][3]


def test_schedule_cancel_retry_and_expiry(client, fake, clip, monkeypatch):
    clip, queued = clip
    tg = client.post("/social/telegram", json={"bot_token": TG_TOKEN, "chat_id": "@micanal"}).json()
    when = datetime.now(timezone.utc) + timedelta(hours=2)
    pub = client.post(f"/clips/{clip['id']}/publish", json={"account_ids": [tg["id"]], "caption": "luego",
                                                            "scheduled_at": when.isoformat()}).json()[0]
    assert pub["status"] == "scheduled" and queued == []
    assert tasks.dispatch_due.run() == 0                      # not yet
    db = SessionLocal()
    db.get(Publication, uuid.UUID(pub["id"])).scheduled_at = datetime.now(timezone.utc) - timedelta(seconds=5)
    db.commit()
    db.close()
    assert tasks.dispatch_due.run() == 1 and queued == [pub["id"]]

    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert client.post(f"/clips/{clip['id']}/publish", json={"account_ids": [tg["id"]], "scheduled_at": past}
                       ).json()["detail"] == "schedule_in_past"
    later = client.post(f"/clips/{clip['id']}/publish", json={"account_ids": [tg["id"]],
                                                              "scheduled_at": when.isoformat()}).json()[0]
    assert client.delete(f"/publications/{later['id']}").json()["status"] == "canceled"
    assert client.delete(f"/publications/{later['id']}").status_code == 409

    # Telegram down: retried (3 attempts), then failed; retry by hand works once it's back.
    retries = []
    monkeypatch.setattr(tasks.publish_post, "apply_async", lambda args, countdown: retries.append(countdown))
    fake.fail["/sendVideo"] = 502
    for _ in range(3):
        tasks.publish_post.run(pub["id"])
    p = client.get(f"/clips/{clip['id']}/publications").json()
    p = next(x for x in p if x["id"] == pub["id"])
    assert len(retries) == 2 and p["status"] == "failed" and p["attempts"] == 3
    del fake.fail["/sendVideo"]
    assert client.post(f"/publications/{pub['id']}/retry").json()["status"] == "publishing"
    tasks.publish_post.run(pub["id"])
    assert next(x for x in client.get("/publications").json() if x["id"] == pub["id"])["status"] == "published"

    # Revoked permission: the account is marked for reconnecting.
    fake.fail["/sendVideo"] = 401
    again = client.post(f"/clips/{clip['id']}/publish", json={"account_ids": [tg["id"]]}).json()[0]
    tasks.publish_post.run(again["id"])
    acc = next(a for a in client.get("/social/accounts").json() if a["id"] == tg["id"])
    assert acc["status"] == "expired"


def test_tiktok_token_refresh_and_direct_mode(client, fake, clip, monkeypatch):
    clip, queued = clip
    _, state = oauth(client, "tiktok")
    client.get(f"/social/callback/tiktok?code=C&state={state}", follow_redirects=False)
    db = SessionLocal()
    acc = db.query(SocialAccount).filter_by(platform="tiktok").one()
    acc.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)   # expired access token
    db.commit()
    acc_id = str(acc.id)
    db.close()
    monkeypatch.setattr(get_settings(), "tiktok_post_mode", "direct")
    r = client.post(f"/clips/{clip['id']}/publish", json={"account_ids": [acc_id]})
    assert r.json()["detail"] == "tiktok_privacy_required"     # TikTok requires an explicit choice
    fake.tiktok_status = "PUBLISH_COMPLETE"
    pub = client.post(f"/clips/{clip['id']}/publish", json={
        "account_ids": [acc_id], "caption": "hola", "options": {"privacy_level": "SELF_ONLY", "allow_duet": False}}).json()[0]
    tasks.publish_post.run(pub["id"])
    assert fake.find("grant_type=refresh_token") or any(b"refresh_token" in c[3] for c in fake.calls)
    init = fake.find("/post/publish/video/init/")[0][3].decode()
    assert '"privacy_level":"SELF_ONLY"' in init.replace(" ", "") and '"disable_duet":true' in init.replace(" ", "")
    assert fake.find("/video/init/")[0][2]["authorization"] == "Bearer TT_REFRESHED"
    out = next(x for x in client.get("/publications").json() if x["id"] == pub["id"])
    assert out["status"] == "published"

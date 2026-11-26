"""AI in the real pipeline (ffmpeg, rendering, covers); Claude is mocked at HTTP level."""
import shutil
import subprocess
import uuid

import pytest

from app.ai import providers
from app.config import get_settings
from app.db import SessionLocal
from app.models import AIUsage, Clip, Job, MediaAsset
from app.storage import get_storage
from app.worker import tasks
from tests.ai_mocks import MockAI, ideas
from tests.conftest import login, make_user
from tests.media_fixtures import fake_transcript, make_video


@pytest.fixture(scope="module")
def video(tmp_path_factory):
    return make_video(tmp_path_factory.mktemp("aiv") / "talk.mp4")


@pytest.fixture
def env(monkeypatch, video):
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "sk-ant-test")

    def fake_download(url, dest, on_progress=None, max_bytes=None):
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy(video, dest / "source.mp4")
        return dest / "source.mp4", {"title": "Charla", "duration": 180}

    monkeypatch.setattr(tasks, "download", fake_download)
    monkeypatch.setattr(tasks, "transcribe", lambda audio, duration, on_progress=None, language=None:
                        fake_transcript(duration))
    monkeypatch.setattr(tasks, "analyze_video", lambda *a, **k: {"fps": 4, "size": [640, 360], "samples": [],
                                                                  "track_person": {}, "people": []})
    covers = []
    monkeypatch.setattr("app.routers.clips.enqueue_cover", lambda cid: covers.append(str(cid)))
    return covers


def use(monkeypatch, mock):
    monkeypatch.setitem(providers.HTTP_CLIENTS, mock.provider, mock.client())


def size(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0",
                          str(path)], capture_output=True, text=True).stdout.strip()
    return tuple(int(v) for v in out.split(","))


def test_ai_selected_clips_with_captions_and_covers(client, env, monkeypatch):
    mock = MockAI(ideas((20, 49.8), (100, 129.8), title="No lo vas a creer"))
    use(monkeypatch, mock)
    make_user("u@x.com")
    login(client, "u@x.com")
    client.put("/ai/prefs", json={"use_ai": True, "rules": "Siempre en tono positivo", "hashtags": 3})
    assert client.get("/ai/status").json()["available"][0]["id"] == "anthropic"
    job_id = client.post("/jobs", json={"url": "https://youtu.be/ai1", "clip_count": 2, "clip_duration": 30,
                                        "ai": {"rules": "Solo momentos de humor"}}).json()["id"]
    tasks.process_job.run(job_id)
    job = client.get(f"/jobs/{job_id}").json()
    assert job["status"] == "done" and job["ai_status"] == "ai:anthropic"
    request_text = mock.requests[0]["body"]["messages"][0]["content"][1]["text"]
    assert "Siempre en tono positivo" in request_text and "Solo momentos de humor" in request_text
    assert "Usa 3 hashtags" in request_text
    clips = sorted(client.get(f"/jobs/{job_id}/clips").json(), key=lambda c: c["start"])
    assert [(c["start"], c["end"]) for c in clips] == [(20.0, 49.8), (100.0, 129.8)]
    assert clips[0]["title"].startswith("No lo vas a creer") and clips[0]["ai_provider"] == "anthropic"
    assert clips[0]["hashtags"] == ["podcast", "humor"] and "IA" in clips[0]["reasons"]
    r = client.get(f"/clips/{clips[0]['id']}/cover")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    db = SessionLocal()
    c = db.get(Clip, uuid.UUID(clips[0]["id"]))
    assert size(get_storage().local_path(c.cover_key)) == (1080, 1920)
    usage = db.query(AIUsage).all()
    assert len(usage) == 1 and usage[0].purpose == "select" and usage[0].output_tokens == 80
    db.close()

    # Edit the title -> cover redrawn by the worker (no re-render).
    r = client.patch(f"/clips/{clips[0]['id']}", json={"title": "Otro título", "hashtags": ["#a", "b"]})
    assert r.json()["title"] == "Otro título" and r.json()["hashtags"] == ["a", "b"]
    assert env == [clips[0]["id"]]
    old_cover = c.cover_key
    tasks.refresh_cover.run(clips[0]["id"])
    db = SessionLocal()
    c = db.get(Clip, uuid.UUID(clips[0]["id"]))
    assert c.cover_key != old_cover and not get_storage().local_path(old_cover).exists()
    db.close()

    # "Otro texto": new caption from the AI with only the clip's words.
    use(monkeypatch, MockAI({"title": "Nuevo", "caption": "Texto nuevo.", "hashtags": ["uno", "#dos"]}))
    r = client.post(f"/clips/{clips[1]['id']}/caption").json()
    assert r["title"] == "Nuevo" and r["hashtags"] == ["uno", "dos"]


def test_ai_failure_falls_back_and_says_why(client, env, monkeypatch):
    use(monkeypatch, MockAI(ideas(), status=500))
    monkeypatch.setattr("anthropic._constants.INITIAL_RETRY_DELAY", 0.0, raising=False)
    monkeypatch.setattr("anthropic._constants.MAX_RETRY_DELAY", 0.0, raising=False)
    make_user("u@x.com")
    login(client, "u@x.com")
    job_id = client.post("/jobs", json={"url": "https://youtu.be/ai2", "clip_count": 2}).json()["id"]
    tasks.process_job.run(job_id)
    job = client.get(f"/jobs/{job_id}").json()
    assert job["status"] == "done" and job["ai_status"] == "fallback:ai_unavailable"
    clips = client.get(f"/jobs/{job_id}/clips").json()
    assert len(clips) == 2 and all(c["title"] and c["ai_provider"] is None for c in clips)


def test_ai_off_for_a_project(client, env, monkeypatch):
    mock = MockAI(ideas((20, 49.8)))
    use(monkeypatch, mock)
    make_user("u@x.com")
    login(client, "u@x.com")
    job_id = client.post("/jobs", json={"url": "https://youtu.be/ai3", "clip_count": 1,
                                        "ai": {"use_ai": False}}).json()["id"]
    tasks.process_job.run(job_id)
    assert client.get(f"/jobs/{job_id}").json()["ai_status"] == "off"
    assert mock.requests == []          # nothing was sent anywhere


def _window_asset(video, user_email="u@x.com"):
    from app.models import LiveSession, User
    db = SessionLocal()
    user = db.query(User).filter(User.email == user_email).one()
    job = Job(user_id=user.id, source="live", title="Directo", clip_count=0, clip_duration=30, render={}, ai={})
    db.add(job)
    db.flush()
    live = LiveSession(user_id=user.id, url="https://www.twitch.tv/x", platform="twitch", job_id=job.id,
                       clip_duration=30, render={})
    db.add(live)
    key = f"users/{user.id}/live/{live.id}/review/w.mp4"
    get_storage().put_file(key, video)
    asset = MediaAsset(user_id=user.id, source_key=f"live:{uuid.uuid4()}", storage_key=key)
    db.add(asset)
    db.commit()
    ids = str(live.id), str(asset.id), str(job.id)
    db.close()
    return ids


def test_live_ai_review(client, env, monkeypatch, video):
    make_user("u@x.com")
    use(monkeypatch, MockAI(ideas((60, 89.8), title="Momentazo")))
    sid, aid, job_id = _window_asset(video)
    tasks.live_ai_review.run(sid, aid, 600.0)       # this stretch starts at minute 10 of the stream
    db = SessionLocal()
    clips = db.query(Clip).filter(Clip.job_id == uuid.UUID(job_id)).all()
    assert len(clips) == 1 and clips[0].render_status == "ready" and clips[0].title.startswith("Momentazo")
    assert clips[0].offset == 600.0 and clips[0].start == 60.0      # shown as 11:00 in the broadcast
    db.close()

    # Nothing good: no clip, and the stretch is deleted to save storage.
    use(monkeypatch, MockAI({"clips": []}))
    sid2, aid2, job2 = _window_asset(video)
    tasks.live_ai_review.run(sid2, aid2, 0.0)
    db = SessionLocal()
    assert db.query(Clip).filter(Clip.job_id == uuid.UUID(job2)).count() == 0
    assert db.get(MediaAsset, uuid.UUID(aid2)) is None
    db.close()

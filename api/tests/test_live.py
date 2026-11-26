"""Live mode end to end against a simulated stream (real-time HLS) and a fake
Twitch chat. Whisper and the face models are stubbed; everything else is real:
streamlink, ffmpeg segmenting, detection, clip windows, rendering."""
import threading
import time
import uuid

import pytest

from app.db import SessionLocal
from app.models import Clip, Job, LiveSession
from tests.conftest import login, make_user
from tests.live_fixtures import FakeLiveStream, FakeTwitchChat
from tests.media_fixtures import fake_transcript


@pytest.fixture
def stream(tmp_path, video):
    s = FakeLiveStream(video, tmp_path / "hls")
    s.start()
    yield s
    s.close()


@pytest.fixture(scope="module")
def video(tmp_path_factory):
    from tests.media_fixtures import make_video
    return make_video(tmp_path_factory.mktemp("lv") / "talk.mp4")


@pytest.fixture
def chat(monkeypatch):
    srv = FakeTwitchChat()
    from app.live import chat as chat_mod
    from app.live import controller

    class Settings:
        twitch_irc_host, twitch_irc_port, twitch_irc_tls = "127.0.0.1", srv.port, False

    monkeypatch.setattr(chat_mod, "get_settings", lambda: Settings)
    monkeypatch.setattr(controller, "make_chat",
                        lambda platform, url, on_msg, on_status, stop: chat_mod.TwitchChat("canal", on_msg, on_status, stop))
    yield srv
    srv.close()


@pytest.fixture
def stubs(monkeypatch):
    from app.worker import tasks
    monkeypatch.setattr(tasks, "transcribe", lambda audio, duration, on_progress=None, language=None:
                        fake_transcript(duration))
    monkeypatch.setattr(tasks, "analyze_video", lambda *a, **k: {"fps": 4, "size": [640, 360], "samples": [],
                                                                  "track_person": {}, "people": []})
    rendered = []
    monkeypatch.setattr("app.live.controller.enqueue_live_clip", lambda cid: rendered.append(str(cid)), raising=False)
    monkeypatch.setattr("app.worker.enqueue_live_clip", lambda cid: rendered.append(str(cid)))
    finals = []
    monkeypatch.setattr("app.worker.enqueue_job", lambda jid: finals.append(str(jid)))
    return rendered, finals


def wait_for(cond, timeout, step=0.5):
    end = time.time() + timeout
    while time.time() < end:
        v = cond()
        if v:
            return v
        time.sleep(step)
    raise AssertionError("timed out")


def test_live_session_end_to_end(client, stream, chat, stubs):
    from app.live.controller import LiveController
    from app.worker import tasks

    rendered, finals = stubs
    make_user("u@x.com")
    login(client, "u@x.com")
    r = client.post("/lives", json={"url": stream.url, "clip_duration": 15, "final_clips": 1,
                                    "sensitivity": "normal"})
    assert r.status_code == 201, r.text
    live = r.json()
    assert client.post("/lives", json={"url": stream.url}).json()["detail"] == "too_many_lives"

    th = threading.Thread(target=LiveController(live["id"]).run, daemon=True)
    th.start()
    chat.joined.wait(15)
    wait_for(lambda: client.get(f"/lives/{live['id']}").json()["status"] == "recording", 40)
    assert client.get(f"/lives/{live['id']}").json()["chat_status"] == "connected"

    # Normal chat for a while, then it explodes with laughter -> automatic clip.
    t0 = time.time()
    while time.time() - t0 < 40:
        chat.say("hola a todos")
        time.sleep(2)
    for _ in range(6):
        for _ in range(15):
            chat.say("JAJAJAJA KEKW")
        time.sleep(1)
    wait_for(lambda: len(rendered) >= 1, 40)
    db = SessionLocal()
    auto = db.get(Clip, uuid.UUID(rendered[0]))
    assert "chat" in auto.reasons and auto.asset_id is not None
    db.close()

    # "¡Clip ahora!" -> the last 15 seconds.
    assert client.post(f"/lives/{live['id']}/clip", json={"seconds": 15}).status_code == 202
    wait_for(lambda: len(rendered) >= 2, 40)

    # The video worker renders them (on its own source, transcribed + reframed).
    for cid in rendered:
        tasks.make_live_clip.run(cid)
    clips = client.get(f"/jobs/{live['job_id']}/clips").json()
    assert len(clips) == 2 and all(c["render_status"] == "ready" for c in clips)
    now = next(c for c in clips if "clip ahora" in c["reasons"])
    assert 14 <= now["duration"] <= 19
    assert now["offset"] > 0               # position within the broadcast
    assert client.get(f"/clips/{now['id']}/video").status_code == 200

    # Stop -> the whole recording goes to the normal pipeline for "lo mejor".
    client.post(f"/lives/{live['id']}/stop")
    th.join(timeout=120)
    final = client.get(f"/lives/{live['id']}").json()
    assert final["status"] == "ended" and final["end_reason"] == "stopped"
    assert final["recorded_seconds"] >= 70 and final["final_job_id"] == finals[0]
    tasks.process_job.run(finals[0])
    db = SessionLocal()
    job = db.get(Job, uuid.UUID(finals[0]))
    assert job.status.value == "done", job.error
    assert db.query(Clip).filter(Clip.job_id == job.id).count() == 1
    assert db.get(Job, uuid.UUID(live["job_id"])).status.value == "done"
    db.close()


def test_live_reconnects_after_drop_and_ends_when_offline(client, stream, stubs):
    from app.live.controller import LiveController

    make_user("u@x.com")
    login(client, "u@x.com")
    live = client.post("/lives", json={"url": stream.url, "final_clips": 0, "auto_clips": False}).json()
    statuses = []
    from app.live import controller as ctl
    orig = ctl.LiveController._db_update

    def spy(self, **fields):
        if "status" in fields:
            statuses.append(fields["status"])
        return orig(self, **fields)

    ctl.LiveController._db_update = spy
    try:
        th = threading.Thread(target=LiveController(live["id"]).run, daemon=True)
        th.start()
        wait_for(lambda: "recording" in statuses, 40)
        time.sleep(12)
        stream.drop()                       # the streamer's connection dies
        wait_for(lambda: "reconnecting" in statuses, 60)
        stream.start()                      # ...and comes back
        wait_for(lambda: statuses.count("recording") >= 2, 90)
        time.sleep(12)
        stream.end()                        # broadcast over
        th.join(timeout=180)
    finally:
        ctl.LiveController._db_update = orig
    db = SessionLocal()
    s = db.get(LiveSession, uuid.UUID(live["id"]))
    assert s.status == "ended" and s.end_reason == "ended"
    assert s.recorded_seconds >= 20
    db.close()


def test_live_rejects_unsupported_urls(client):
    make_user("u@x.com")
    login(client, "u@x.com")
    assert client.post("/lives", json={"url": "https://example.com/x"}).json()["detail"] == "unsupported_live_url"
    r = client.post("/lives", json={"url": "https://www.twitch.tv/canal"})
    assert r.status_code == 201 and r.json()["platform"] == "twitch"

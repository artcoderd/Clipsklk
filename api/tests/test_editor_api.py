import io
import shutil
import subprocess
import uuid

import cv2
import numpy as np
import pytest

from app.db import SessionLocal
from app.media import ffmpeg as F
from app.models import Clip
from app.storage import get_storage
from app.worker import tasks
from tests.conftest import login, make_user
from tests.media_fixtures import fake_transcript, make_video


@pytest.fixture(scope="module")
def video(tmp_path_factory):
    return make_video(tmp_path_factory.mktemp("ed") / "talk.mp4")


@pytest.fixture
def made_clip(client, monkeypatch, video):
    def fake_download(url, dest, on_progress=None, max_bytes=None):
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy(video, dest / "source.mp4")
        return dest / "source.mp4", {"title": "Charla", "duration": 180}

    monkeypatch.setattr(tasks, "download", fake_download)
    monkeypatch.setattr(tasks, "transcribe", lambda audio, duration, on_progress=None, language=None:
                        fake_transcript(duration))
    monkeypatch.setattr(tasks, "analyze_video", lambda *a, **k: {"fps": 4, "size": [640, 360], "samples": [],
                                                                  "track_person": {}, "people": []})
    queued = []
    monkeypatch.setattr("app.routers.editor.enqueue_proxy", lambda cid: queued.append(str(cid)))
    monkeypatch.setattr("app.routers.clips.enqueue_rerender", lambda cid: queued.append(("render", str(cid))))
    make_user("u@x.com")
    login(client, "u@x.com")
    job_id = client.post("/jobs", json={"url": "https://youtu.be/ed1", "clip_count": 1, "clip_duration": 30}).json()["id"]
    tasks.process_job.run(job_id)
    clip = client.get(f"/jobs/{job_id}/clips").json()[0]
    return clip, queued


def upload(client, kind, name, data, ctype):
    return client.post("/library", data={"kind": kind}, files={"file": (name, io.BytesIO(data), ctype)}).json()


def png(color):
    img = np.zeros((80, 80, 4), np.uint8)
    img[:, :, :3] = color
    img[:, :, 3] = 255
    return cv2.imencode(".png", img)[1].tobytes()


def wav(freq, dur=0.5):
    return subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"sine=f={freq}:d={dur}", "-f", "wav", "-"],
                          capture_output=True).stdout


def test_editor_flow(client, made_clip):
    clip, queued = made_clip
    cid = clip["id"]
    data = client.get(f"/clips/{cid}/editor").json()
    lo, hi = data["bounds"]
    assert lo == max(0, clip["start"] - 20) and data["edit"]["trim"] == [clip["start"], clip["end"]]
    assert data["words"] and all(lo <= w["start"] < hi for w in data["words"])
    assert len(data["level"]) >= int(hi - lo) and data["proxy"]["status"] == "none"

    # Preview copy: half size, covers the stretch around the clip.
    assert client.post(f"/clips/{cid}/editor/prepare").json()["status"] == "preparing"
    assert client.post(f"/clips/{cid}/editor/prepare").json()["status"] == "preparing"   # not queued twice
    assert queued == [cid]
    tasks.prepare_proxy.run(cid)
    data = client.get(f"/clips/{cid}/editor").json()
    assert data["proxy"]["status"] == "ready" and data["proxy"]["url"]
    db = SessionLocal()
    c = db.get(Clip, uuid.UUID(cid))
    proxy = get_storage().local_path(c.proxy_key)
    db.close()
    meta = F.probe(proxy)
    assert (meta["width"], meta["height"]) == (540, 960) and abs(meta["duration"] - (hi - lo)) < 0.5
    assert client.get(data["proxy"]["url"].removeprefix("/api")).status_code == 200
    assert client.post(f"/clips/{cid}/editor/prepare").json()["status"] == "ready"

    # Save an edit with a text, an image, a sound and music from the library.
    image = upload(client, "image", "sticker.png", png((0, 0, 255)), "image/png")
    sound = upload(client, "sound", "boom.wav", wav(2500), "audio/wav")
    music = upload(client, "music", "song.wav", wav(330, 3), "audio/wav")
    a, b = clip["start"], clip["end"]
    doc = {"trim": [a - 5, b], "cuts": [[a + 2, a + 6], [a + 5, a + 8]],
           "texts": [{"id": "t1", "text": "¡Atención!", "start": a, "end": a + 10, "x": 0.5, "y": 0.2}],
           "images": [{"id": "i1", "library_id": image["id"], "start": a + 10, "end": b + 50, "x": 0.5, "y": 0.7, "w": 0.3}],
           "sounds": [{"id": "s1", "library_id": sound["id"], "t": a + 9}],
           "markers": [{"id": "m1", "t": a + 12, "label": "chiste"}],
           "music": {"library_id": music["id"], "volume": 0.2}}
    saved = client.put(f"/clips/{cid}/edit", json=doc).json()
    assert saved["cuts"] == [[a + 2, a + 8]]                       # overlapping cuts merged
    assert saved["images"][0]["end"] == b                          # kept inside the trim
    assert saved["images"][0]["end"] - saved["images"][0]["start"] == pytest.approx(b - (a + 10))
    # An item placed before the trim is moved in (with its length), not dropped.
    early = client.put(f"/clips/{cid}/edit", json={**doc, "texts": [{**doc["texts"][0], "start": a - 30, "end": a - 27}]}).json()
    assert early["texts"][0]["start"] == a - 5 and early["texts"][0]["end"] == pytest.approx(a - 2)
    saved = client.put(f"/clips/{cid}/edit", json=doc).json()
    c = client.get(f"/clips/{cid}").json()
    assert c["edited"] and c["start"] == a - 5                     # clip range follows the trim

    # Render with the edit: duration = trim minus cuts.
    assert client.post(f"/clips/{cid}/render", json={}).status_code == 200
    tasks.rerender_clip.run(cid)
    db = SessionLocal()
    c = db.get(Clip, uuid.UUID(cid))
    out = get_storage().local_path(c.storage_key)
    db.close()
    expected = (b - (a - 5)) - 6
    assert abs(F.probe(out)["duration"] - expected) < 0.3

    # Reset: back to the original clip range.
    client.put(f"/clips/{cid}/edit", json={**doc, "trim": [a + 1, b - 1]})   # a second edit keeps the first original
    client.delete(f"/clips/{cid}/edit")
    c = client.get(f"/clips/{cid}").json()
    assert c["edited"] is False and (c["start"], c["end"]) == (a, b)


def test_edit_validation(client, made_clip):
    clip, _ = made_clip
    cid, a, b = clip["id"], clip["start"], clip["end"]
    r = client.put(f"/clips/{cid}/edit", json={"trim": [a, a + 0.5]})
    assert r.json()["detail"] == "edit_too_short"
    r = client.put(f"/clips/{cid}/edit", json={"trim": [a, b], "cuts": [[a, b - 0.5]]})
    assert r.json()["detail"] == "edit_too_short"
    r = client.put(f"/clips/{cid}/edit", json={"trim": [a, b], "images": [
        {"id": "x", "library_id": str(uuid.uuid4()), "start": a, "end": b, "x": 0.5, "y": 0.5, "w": 0.2}]})
    assert r.json()["detail"] == "invalid_library_item"
    r = client.put(f"/clips/{cid}/edit", json={"trim": [a, b], "texts": [{"id": "t", "text": "x" * 500, "start": a,
                                                                          "end": b, "x": 0.5, "y": 0.5}]})
    assert r.status_code == 422
    # Another user's clip / library items are invisible.
    make_user("v@x.com")
    client.cookies.clear()
    login(client, "v@x.com")
    assert client.get(f"/clips/{cid}/editor").status_code == 404
    assert client.put(f"/clips/{cid}/edit", json={"trim": [a, b]}).status_code == 404

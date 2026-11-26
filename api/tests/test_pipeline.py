"""End-to-end pipeline with real ffmpeg; only Whisper and yt-dlp are stubbed."""
import json
import shutil
import subprocess
import uuid

import pytest

from app.db import SessionLocal
from app.media import PipelineError
from app.models import Clip, Job, MediaAsset
from app.worker import tasks
from tests.conftest import login, make_user
from tests.media_fixtures import LAUGH_AT, SCREAM_AT, fake_transcript, make_video

FAKE_VISION: dict = {}


@pytest.fixture(scope="session")
def video(tmp_path_factory):
    return make_video(tmp_path_factory.mktemp("media") / "talk.mp4")


@pytest.fixture
def calls(monkeypatch, video):
    calls = {"download": 0, "transcribe": 0, "vision": 0}

    def fake_download(url, dest, on_progress=None, max_bytes=None):
        calls["download"] += 1
        dest.mkdir(parents=True, exist_ok=True)
        out = dest / "source.mp4"
        shutil.copy(video, out)
        on_progress and on_progress(1.0)
        return out, {"title": "Mi podcast", "duration": 180}

    def fake_transcribe(audio, duration, on_progress=None, language=None):
        calls["transcribe"] += 1
        return fake_transcript(duration)

    def fake_vision(src, width, height, duration, on_progress=None, hwaccel=False):
        calls["vision"] += 1
        return FAKE_VISION.get("value") or {"fps": 4, "size": [640, 360], "samples": [],
                                            "track_person": {}, "people": []}

    monkeypatch.setattr(tasks, "download", fake_download)
    monkeypatch.setattr(tasks, "transcribe", fake_transcribe)
    monkeypatch.setattr(tasks, "analyze_video", fake_vision)
    FAKE_VISION.clear()
    return calls


def _run(job_id):
    tasks.process_job.run(job_id)
    db = SessionLocal()
    job = db.get(Job, uuid.UUID(job_id))
    clips = list(db.query(Clip).filter(Clip.job_id == job.id).order_by(Clip.start))
    db.close()
    return job, clips


def _size(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height", "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return tuple(int(v) for v in out.stdout.strip().split(","))


def _duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
                          str(path)], capture_output=True, text=True).stdout
    data = json.loads(out)
    kinds = {s["codec_type"] for s in data["streams"]}
    return float(data["format"]["duration"]), kinds


def test_url_job_end_to_end(client, calls, _env):
    make_user("u@x.com")
    login(client, "u@x.com")
    job_id = client.post("/jobs", json={"url": "https://www.youtube.com/watch?v=abc123&t=30",
                                        "clip_count": 3, "clip_duration": 30}).json()["id"]
    job, clips = _run(job_id)
    assert job.status.value == "done" and job.progress == 100, job.error
    assert job.title == "Mi podcast"
    assert len(clips) == 3
    # The scream and the laughter both made it into clips.
    assert any(c.start <= SCREAM_AT[0] and c.end >= SCREAM_AT[1] for c in clips)
    assert any(c.start <= LAUGH_AT[0] and c.end >= LAUGH_AT[1] for c in clips)

    from app.storage import get_storage
    for c in clips:
        dur, kinds = _duration(get_storage().local_path(c.storage_key))
        assert abs(dur - (c.end - c.start)) < 0.6 and kinds == {"video", "audio"}
        assert _size(get_storage().local_path(c.storage_key)) == (1080, 1920)   # vertical by default
        assert c.layout == "fit" and c.render_status == "ready" and c.version == 1
        assert get_storage().local_path(c.thumbnail_key).stat().st_size > 1000

    # API: list, stream with Range (needed by phones), download as attachment.
    listed = client.get(f"/jobs/{job_id}/clips").json()
    assert len(listed) == 3 and listed[0]["score"] >= listed[-1]["score"]
    r = client.get(f"/clips/{listed[0]['id']}/video", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100
    r = client.get(f"/clips/{listed[0]['id']}/video?download=1")
    assert "attachment" in r.headers["content-disposition"] and "Mi-podcast-clip" in r.headers["content-disposition"]
    assert client.get(f"/clips/{listed[0]['id']}/thumbnail").headers["content-type"] == "image/jpeg"

    # More clips: no new download or transcription, and no overlap with earlier clips.
    assert client.post(f"/jobs/{job_id}/more", json={"count": 2}).status_code == 200
    job, all_clips = _run(job_id)
    assert job.status.value == "done" and len(all_clips) == 5
    assert calls == {"download": 1, "transcribe": 1, "vision": 1}
    spans = sorted((c.start, c.end) for c in all_clips)
    assert all(b[0] >= a[1] for a, b in zip(spans, spans[1:]))

    # Same video via another link form: reuses the stored asset.
    job2 = client.post("/jobs", json={"url": "https://youtu.be/abc123", "clip_count": 1}).json()["id"]
    job, clips = _run(job2)
    assert job.status.value == "done" and len(clips) == 1
    assert calls == {"download": 1, "transcribe": 1, "vision": 1}
    db = SessionLocal()
    assert db.query(MediaAsset).count() == 1
    db.close()


def test_upload_job_end_to_end(client, calls, video):
    make_user("u@x.com")
    login(client, "u@x.com")
    with open(video, "rb") as f:
        job_id = client.post("/jobs/upload", files={"file": ("talk.mp4", f, "video/mp4")},
                             data={"clip_count": "2", "clip_duration": "45"}).json()["id"]
    job, clips = _run(job_id)
    assert job.status.value == "done", job.error
    assert len(clips) == 2 and calls["download"] == 0


def test_clips_are_private(client, calls):
    make_user("a@x.com")
    make_user("b@x.com")
    login(client, "a@x.com")
    job_id = client.post("/jobs", json={"url": "https://kick.com/video/1", "clip_count": 1}).json()["id"]
    _run(job_id)
    clip_id = client.get(f"/jobs/{job_id}/clips").json()[0]["id"]
    client.cookies.clear()
    login(client, "b@x.com")
    assert client.get(f"/clips/{clip_id}/video").status_code == 404
    assert client.get(f"/jobs/{job_id}/clips").status_code == 404


def test_download_errors_are_reported(client, monkeypatch):
    def blocked(*a, **k):
        raise PipelineError("source_blocked")
    monkeypatch.setattr(tasks, "download", blocked)
    make_user("u@x.com")
    login(client, "u@x.com")
    job_id = client.post("/jobs", json={"url": "https://youtu.be/zzz"}).json()["id"]
    job, _ = _run(job_id)
    assert job.status.value == "failed" and job.error == "source_blocked"


def test_audio_only_upload(client, calls, tmp_path):
    from tests.media_fixtures import synth_audio, write_wav
    wav = write_wav(tmp_path / "pod.wav", synth_audio(90))
    make_user("u@x.com")
    login(client, "u@x.com")
    with open(wav, "rb") as f:
        job_id = client.post("/jobs/upload", files={"file": ("pod.wav", f, "audio/wav")},
                             data={"clip_count": "1", "clip_duration": "30"}).json()["id"]
    job, clips = _run(job_id)
    assert job.status.value == "done", job.error
    from app.storage import get_storage
    _, kinds = _duration(get_storage().local_path(clips[0].storage_key))
    assert kinds == {"video", "audio"}


def test_render_options_rerender_styles_and_library(client, calls, tmp_path):
    from app.storage import get_storage
    make_user("u@x.com")
    login(client, "u@x.com")
    # A saved subtitle style and a logo from the user's library.
    style = client.post("/styles", json={"name": "Mío", "config": {"font": "bebas", "highlight_color": "#00FF00",
                                                                    "position": "top"}}).json()
    assert {s["id"] for s in client.get("/styles").json()} >= {"preset:clasico", style["id"]}
    import cv2
    import numpy as np
    logo_path = tmp_path / "logo.png"
    cv2.imwrite(str(logo_path), np.full((50, 120, 4), 255, np.uint8))
    with open(logo_path, "rb") as f:
        logo = client.post("/library", data={"kind": "logo", "name": "Logo"},
                           files={"file": ("logo.png", f, "image/png")}).json()
    assert client.get("/library?kind=logo").json()[0]["id"] == logo["id"]

    render = {"format": "1:1", "style_id": style["id"], "logo_id": logo["id"], "subtitles": True}
    job_id = client.post("/jobs", json={"url": "https://kick.com/video/9", "clip_count": 1, "clip_duration": 20,
                                        "render": render}).json()["id"]
    job, clips = _run(job_id)
    assert job.status.value == "done", job.error
    path = get_storage().local_path(clips[0].storage_key)
    assert _size(path) == (1080, 1080)

    # Re-render the same clip as 16:9 without subtitles: new version, old file removed.
    clip_id = str(clips[0].id)
    r = client.post(f"/clips/{clip_id}/render", json={"format": "16:9", "subtitles": False})
    assert r.status_code == 200 and r.json()["render_status"] == "rendering"
    assert client.post(f"/clips/{clip_id}/render", json={}).status_code == 409   # already rendering
    tasks.rerender_clip.run(clip_id)
    c = client.get(f"/clips/{clip_id}").json()
    assert c["render_status"] == "ready" and c["version"] == 2 and c["render"]["format"] == "16:9"
    db = SessionLocal()
    new = db.get(Clip, uuid.UUID(clip_id))
    db.close()
    assert _size(get_storage().local_path(new.storage_key)) == (1920, 1080)
    assert not path.exists()
    assert calls["download"] == 1   # re-rendering never downloads again


def test_render_options_only_accept_own_items(client, calls):
    make_user("a@x.com")
    make_user("b@x.com")
    login(client, "a@x.com")
    style_a = client.post("/styles", json={"name": "A", "config": {}}).json()["id"]
    client.cookies.clear()
    login(client, "b@x.com")
    r = client.post("/jobs", json={"url": "https://kick.com/video/1", "render": {"style_id": style_a}})
    assert r.status_code == 400 and r.json()["detail"] == "invalid_style"
    r = client.post("/jobs", json={"url": "https://kick.com/video/1", "render": {"music_id": str(uuid.uuid4())}})
    assert r.json()["detail"] == "invalid_music"
    assert client.put(f"/styles/{style_a}", json={"name": "x", "config": {}}).status_code == 404
    bad = client.post("/styles", json={"name": "x", "config": {"text_color": "red"}})
    assert bad.status_code == 422


def _two_speakers_vision(seconds=180):
    """Person 0 talks during the first half, person 1 during the second."""
    samples = []
    for i in range(seconds * 4):
        t = i / 4
        a0, a1 = (0.3, 0.02) if t < seconds / 2 else (0.02, 0.3)
        samples.append([t, [[0, 0.25, 0.4, 0.1, 0.18, a0], [1, 0.75, 0.4, 0.1, 0.18, a1]]])
    people = [{"id": p, "seconds": seconds, "thumb_t": 10.0, "thumb_box": [x - 0.05, 0.3, 0.1, 0.18]}
              for p, x in ((0, 0.25), (1, 0.75))]
    return {"fps": 4, "size": [640, 360], "samples": samples, "track_person": {"0": 0, "1": 1}, "people": people}


def test_people_and_clips_of_one_person(client, calls):
    FAKE_VISION["value"] = _two_speakers_vision()
    make_user("u@x.com")
    login(client, "u@x.com")
    job_id = client.post("/jobs", json={"url": "https://youtu.be/two", "clip_count": 1,
                                        "clip_duration": 30}).json()["id"]
    job, clips = _run(job_id)
    assert job.status.value == "done", job.error
    assert clips[0].layout == "split"        # two people far apart -> split screen
    people = client.get(f"/jobs/{job_id}/people").json()
    assert [p["id"] for p in people] == [0, 1]
    assert client.get(f"/jobs/{job_id}/people/1/thumbnail").headers["content-type"] == "image/jpeg"
    assert client.get(f"/jobs/{job_id}/preview").status_code == 200

    assert client.post(f"/jobs/{job_id}/more", json={"count": 2, "person": 7}).json()["detail"] == "person_not_found"
    assert client.post(f"/jobs/{job_id}/more", json={"count": 2, "person": 1}).status_code == 200
    job, all_clips = _run(job_id)
    assert job.status.value == "done", job.error
    assert job.focus_person is None          # one-off filter, cleared afterwards
    new = [c for c in all_clips if "persona" in c.reasons]
    # The second half is 90 s and the first clip may already use part of it.
    assert 1 <= len(new) <= 2
    for c in new:
        assert c.start >= 88 and c.render["person"] == 1   # only from person 1's half
        assert c.layout == "follow"                         # camera on that person, not split
    assert calls["vision"] == 1


def test_real_vision_runs_on_video_without_faces(client, monkeypatch, video):
    """The real face pipeline (OpenCV models) end to end, on a video with no faces."""
    from app.media.vision import analyze_video
    v = analyze_video(video, 640, 360, 20.0)
    assert v["people"] == [] and len(v["samples"]) >= 70

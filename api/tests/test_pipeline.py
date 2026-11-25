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


@pytest.fixture(scope="session")
def video(tmp_path_factory):
    return make_video(tmp_path_factory.mktemp("media") / "talk.mp4")


@pytest.fixture
def calls(monkeypatch, video):
    calls = {"download": 0, "transcribe": 0}

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

    monkeypatch.setattr(tasks, "download", fake_download)
    monkeypatch.setattr(tasks, "transcribe", fake_transcribe)
    return calls


def _run(job_id):
    tasks.process_job.run(job_id)
    db = SessionLocal()
    job = db.get(Job, uuid.UUID(job_id))
    clips = list(db.query(Clip).filter(Clip.job_id == job.id).order_by(Clip.start))
    db.close()
    return job, clips


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
    assert calls == {"download": 1, "transcribe": 1}
    spans = sorted((c.start, c.end) for c in all_clips)
    assert all(b[0] >= a[1] for a, b in zip(spans, spans[1:]))

    # Same video via another link form: reuses the stored asset.
    job2 = client.post("/jobs", json={"url": "https://youtu.be/abc123", "clip_count": 1}).json()["id"]
    job, clips = _run(job2)
    assert job.status.value == "done" and len(clips) == 1
    assert calls == {"download": 1, "transcribe": 1}
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

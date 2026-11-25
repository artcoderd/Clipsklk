import io

from app.platforms import detect_platform
from tests.conftest import login, make_user


def test_detect_platform():
    assert detect_platform("https://www.youtube.com/watch?v=x") == "youtube"
    assert detect_platform("https://youtu.be/x") == "youtube"
    assert detect_platform("https://www.twitch.tv/foo") == "twitch"
    assert detect_platform("https://kick.com/foo") == "kick"
    assert detect_platform("https://www.tiktok.com/@a/live") == "tiktok"
    assert detect_platform("https://fb.watch/abc") == "facebook"
    assert detect_platform("https://evil-youtube.com/x") is None
    assert detect_platform("javascript:alert(1)") is None


def test_create_job_from_url(client, _env):
    make_user("u@x.com")
    login(client, "u@x.com")
    r = client.post("/jobs", json={"url": "https://youtu.be/abc", "clip_count": 3, "clip_duration": 30})
    assert r.status_code == 201
    job = r.json()
    assert job["platform"] == "youtube" and job["status"] == "queued"
    assert len(_env) == 1
    assert [j["id"] for j in client.get("/jobs").json()] == [job["id"]]
    assert client.post("/jobs", json={"url": "https://example.com/v"}).status_code == 400


def test_upload_job(client):
    make_user("u@x.com")
    login(client, "u@x.com")
    r = client.post("/jobs/upload", files={"file": ("clip.mp4", io.BytesIO(b"\x00" * 1024), "video/mp4")},
                    data={"clip_count": "2", "clip_duration": "20"})
    assert r.status_code == 201, r.text
    assert r.json()["original_filename"] == "clip.mp4"
    bad = client.post("/jobs/upload", files={"file": ("x.exe", io.BytesIO(b"x"), "application/octet-stream")})
    assert bad.status_code == 400


def test_users_only_see_own_jobs(client):
    make_user("a@x.com")
    make_user("b@x.com")
    login(client, "a@x.com")
    job_id = client.post("/jobs", json={"url": "https://kick.com/x"}).json()["id"]
    client.cookies.clear()
    login(client, "b@x.com")
    assert client.get(f"/jobs/{job_id}").status_code == 404
    assert client.get("/jobs").json() == []


def test_active_job_quota(client):
    make_user("u@x.com")
    login(client, "u@x.com")
    for _ in range(3):
        assert client.post("/jobs", json={"url": "https://kick.com/x"}).status_code == 201
    assert client.post("/jobs", json={"url": "https://kick.com/x"}).status_code == 429


def test_worker_pipeline_runs(client):
    """Run the Celery task body directly against the test DB."""
    from app.db import SessionLocal
    from app.models import Job
    from app.worker.tasks import process_job

    make_user("u@x.com")
    login(client, "u@x.com")
    job_id = client.post("/jobs", json={"url": "https://youtu.be/abc"}).json()["id"]
    process_job.run(job_id, stage_delay=0)
    db = SessionLocal()
    import uuid
    job = db.get(Job, uuid.UUID(job_id))
    assert job.status.value == "done" and job.progress == 100
    db.close()

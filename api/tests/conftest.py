import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["STORAGE_DIR"] = f"{_tmp}/storage"
os.environ["PUBLIC_SIGNUP"] = "false"
os.environ["SECRET_KEY"] = "test-secret-key-that-is-at-least-32-bytes"

import fakeredis  # noqa: E402
import pyotp  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import redis_client  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import Role, User  # noqa: E402
from app.security import hash_password  # noqa: E402

PASSWORD = "supersecret123"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(redis_client, "get_redis", lambda: fake)
    for mod in ("app.ratelimit", "app.events", "app.routers.jobs"):
        monkeypatch.setattr(f"{mod}.get_redis", lambda: fake)
    queued = []
    monkeypatch.setattr("app.routers.jobs.enqueue_job", lambda job_id: queued.append(job_id))
    yield queued


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


def make_user(email, role=Role.user, totp=False):
    db = SessionLocal()
    u = User(email=email, password_hash=hash_password(PASSWORD), role=role)
    if totp:
        u.totp_secret = pyotp.random_base32()
        u.totp_enabled = True
    db.add(u)
    db.commit()
    db.close()
    return u


def login(client, email, totp_secret=None):
    body = {"email": email, "password": PASSWORD}
    if totp_secret:
        body["totp_code"] = pyotp.TOTP(totp_secret).now()
    r = client.post("/auth/login", json=body)
    assert r.status_code == 200, r.text
    return r

import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["STORAGE_DIR"] = f"{_tmp}/storage"
os.environ["WORK_DIR"] = f"{_tmp}/work"
os.environ["LIVE_DIR"] = f"{_tmp}/live"
os.environ["LIVE_ALLOW_GENERIC_HLS"] = "true"
os.environ["LIVE_OFFLINE_GRACE_MINUTES"] = "0.4"
# CLIs installed next to this interpreter (streamlink, yt-dlp) must be on PATH.
import sys  # noqa: E402
os.environ["PATH"] = os.path.dirname(sys.executable) + os.pathsep + os.environ.get("PATH", "")


def _vapid():
    import base64

    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid
    v = Vapid()
    v.generate_keys()
    pub = v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    priv = v.private_key.private_numbers().private_value.to_bytes(32, "big")
    b64 = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")  # noqa: E731
    return b64(pub), b64(priv)


os.environ["VAPID_PUBLIC_KEY"], os.environ["VAPID_PRIVATE_KEY"] = _vapid()
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
    from app.billing import seed
    _db = SessionLocal()
    seed(_db)
    _db.close()
    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(redis_client, "get_redis", lambda: fake)
    for mod in ("app.ratelimit", "app.events", "app.routers.jobs", "app.worker.tasks", "app.routers.lives",
                "app.live.controller"):
        monkeypatch.setattr(f"{mod}.get_redis", lambda: fake)
    queued = []
    monkeypatch.setattr("app.routers.jobs.enqueue_job", lambda job_id: queued.append(job_id))
    monkeypatch.setattr("app.routers.lives.enqueue_live", lambda live_id: queued.append(("live", live_id)))
    yield queued


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


def make_user(email, role=Role.user, totp=False, plan="pro"):
    """Test users are on "pro" unless a test is about plans (billing has its own tests)."""
    from app.models import Subscription
    db = SessionLocal()
    u = User(email=email, password_hash=hash_password(PASSWORD), role=role)
    if totp:
        u.totp_secret = pyotp.random_base32()
        u.totp_enabled = True
    db.add(u)
    db.flush()
    if plan != "free":
        db.add(Subscription(user_id=u.id, plan_id=plan, source="manual", status="active"))
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

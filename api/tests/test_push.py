"""Web push end to end: a local server plays the browser's push service and
the test decrypts what it receives, exactly as the phone would."""
import base64
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import http_ece
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.db import SessionLocal
from app.models import PushSubscription
from tests.conftest import login, make_user


def b64(b):
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


class PushService:
    def __init__(self, status=201):
        self.received, self.status = [], status
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                outer.received.append((dict(self.headers), body))
                self.send_response(outer.status)
                self.end_headers()

            def log_message(self, *a):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/push/device1"


def browser_keys():
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return priv, pub, os.urandom(16)


def test_push_delivers_encrypted_notification(client):
    make_user("u@x.com")
    login(client, "u@x.com")
    assert client.get("/push/key").json()["key"] == os.environ["VAPID_PUBLIC_KEY"]
    svc = PushService()
    priv, pub, auth = browser_keys()
    r = client.post("/push/subscriptions", json={"endpoint": svc.url, "keys": {"p256dh": b64(pub), "auth": b64(auth)}})
    assert r.status_code == 201
    assert client.post("/push/test").json() == {"sent": 1}
    headers, body = svc.received[0]
    headers = {k.lower(): v for k, v in headers.items()}
    assert headers["authorization"].startswith("vapid ")    # signed with our VAPID key
    payload = json.loads(http_ece.decrypt(body, private_key=priv, auth_secret=auth, version="aes128gcm"))
    assert payload["title"] == "Clipeo" and "funcionan" in payload["body"]


def test_expired_subscription_is_removed(client):
    make_user("u@x.com")
    login(client, "u@x.com")
    svc = PushService(status=410)
    _, pub, auth = browser_keys()
    client.post("/push/subscriptions", json={"endpoint": svc.url, "keys": {"p256dh": b64(pub), "auth": b64(auth)}})
    assert client.post("/push/test").json() == {"sent": 0}
    db = SessionLocal()
    assert db.query(PushSubscription).count() == 0
    db.close()

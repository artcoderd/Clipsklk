import json
import time
import uuid

import pytest
import stripe

from app.billing import NoMinutes, balance, charge, subscription
from app.db import SessionLocal
from app.models import Plan, Role, User
from tests.conftest import login, make_user

WH = "whsec_test_secret"


@pytest.fixture
def stripe_env(monkeypatch):
    from app.config import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "stripe_secret_key", "sk_test_x")
    monkeypatch.setattr(s, "stripe_webhook_secret", WH)
    monkeypatch.setattr(s, "whatsapp_number", "5215512345678")
    db = SessionLocal()
    db.get(Plan, "creador").stripe_price_id = "price_creador"
    db.get(Plan, "pro").stripe_price_id = "price_pro"
    from app.models import CreditPack
    db.get(CreditPack, "m60").stripe_price_id = "price_m60"
    db.commit()
    db.close()
    return s


def send(client, event):
    payload = json.dumps(event)
    sig = stripe.WebhookSignature._compute_signature(f"{int(time.time())}.{payload}", WH)
    header = f"t={int(time.time())},v1={sig}"
    return client.post("/billing/webhook", content=payload, headers={"stripe-signature": header,
                                                                     "content-type": "application/json"})


def test_webhook_rejects_bad_signature(client, stripe_env):
    r = client.post("/billing/webhook", content=b"{}", headers={"stripe-signature": "t=1,v1=bad"})
    assert r.status_code == 400


def test_pack_purchase_is_idempotent(client, stripe_env):
    u = make_user("a@x.com", plan="free")
    ev = {"id": "evt_1", "type": "checkout.session.completed", "data": {"object": {
        "id": "cs_1", "customer": "cus_1", "payment_status": "paid",
        "metadata": {"user_id": str(u.id), "kind": "pack", "item": "m60"}}}}
    assert send(client, ev).status_code == 200
    assert send(client, ev).status_code == 200      # Stripe retries: minutes only once
    db = SessionLocal()
    b = balance(db, u.id)
    assert b.credits == 60 and b.allowance == 60
    assert subscription(db, u.id).stripe_customer_id == "cus_1"
    db.close()


def test_subscription_lifecycle(client, stripe_env):
    u = make_user("a@x.com", plan="free")
    now = int(time.time())
    sub = {"id": "sub_1", "customer": "cus_1", "status": "active", "cancel_at_period_end": False,
           "metadata": {"user_id": str(u.id), "plan": "creador"},
           "items": {"data": [{"price": {"id": "price_creador"}, "current_period_start": now,
                               "current_period_end": now + 30 * 86400}]}}
    send(client, {"id": "e1", "type": "customer.subscription.created", "data": {"object": sub}})
    db = SessionLocal()
    s = subscription(db, u.id)
    assert (s.plan_id, s.source) == ("creador", "stripe")
    assert balance(db, u.id).allowance == 600
    db.close()
    # Upgrade from the portal
    sub["items"]["data"][0]["price"]["id"] = "price_pro"
    send(client, {"id": "e2", "type": "customer.subscription.updated", "data": {"object": sub}})
    db = SessionLocal()
    assert subscription(db, u.id).plan_id == "pro"
    db.close()
    # A deleted event for some other, older subscription doesn't touch it
    send(client, {"id": "e3", "type": "customer.subscription.deleted", "data": {"object": {
        **sub, "id": "sub_old"}}})
    db = SessionLocal()
    assert subscription(db, u.id).plan_id == "pro"
    db.close()
    send(client, {"id": "e4", "type": "customer.subscription.deleted", "data": {"object": sub}})
    db = SessionLocal()
    s = subscription(db, u.id)
    assert (s.plan_id, s.source, s.stripe_customer_id) == ("free", "free", "cus_1")
    db.close()


def test_checkout_and_me(client, stripe_env, monkeypatch):
    u = make_user("a@x.com", plan="free")
    login(client, u.email)
    seen = {}

    def fake(db, user, item):
        seen["item"] = item.id
        return "https://checkout.stripe.com/x"
    monkeypatch.setattr("app.payments.checkout_url", fake)
    assert client.post("/billing/checkout", json={"plan_id": "free"}).json()["detail"] == "not_buyable"
    r = client.post("/billing/checkout", json={"pack_id": "m60"})
    assert r.json()["url"].startswith("https://checkout.stripe.com") and seen["item"] == "m60"
    me = client.get("/billing/me").json()
    assert me["plan"]["id"] == "free" and me["left"] == 60 and "wa.me/5215512345678" in me["whatsapp"]
    plans = client.get("/billing/plans").json()
    assert [p["id"] for p in plans["plans"]] == ["free", "creador", "pro"]
    assert plans["plans"][1]["buyable"] is True


def test_manual_plan_and_minutes(client):
    owner = make_user("owner@x.com", Role.owner, totp=True)
    adm = make_user("adm@x.com", Role.admin, totp=True)
    u = make_user("u@x.com", plan="free")
    login(client, adm.email, adm.totp_secret)
    # Only the owner activates payments
    assert client.post(f"/admin/users/{u.id}/plan", json={"plan_id": "pro"}).status_code == 403
    assert client.get("/admin/usage").status_code == 200
    login(client, owner.email, owner.totp_secret)
    r = client.post(f"/admin/users/{u.id}/plan", json={"plan_id": "creador", "months": 2, "amount": "MXN 700"})
    assert r.status_code == 200
    r = client.post(f"/admin/users/{u.id}/minutes", json={"minutes": 90, "note": "regalo"})
    assert r.json()["left"] == 690
    row = next(x for x in client.get("/admin/usage").json() if x["email"] == "u@x.com")
    assert row["plan_id"] == "creador" and row["source"] == "manual" and row["credits"] == 90
    # Expired manual plan → back to free
    db = SessionLocal()
    from datetime import datetime, timedelta, timezone
    s = subscription(db, u.id)
    s.period_end = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()
    assert subscription(db, u.id).plan_id == "free"
    db.commit()
    db.close()
    fin = client.get("/admin/finance").json()
    assert "margin" in fin and fin["income"]["total"] >= 0


def test_charge_plan_then_credits_and_idempotent():
    u = make_user("a@x.com", plan="free")
    db = SessionLocal()
    user = db.get(User, u.id)
    from app.billing import grant
    grant(db, u.id, 10, "purchase", "p1", "x")
    assert charge(db, user, 65, "asset:1") is True
    assert charge(db, user, 65, "asset:1") is False      # same asset, not charged again
    b = balance(db, u.id)
    assert b.plan_left == 0 and b.credits == 5
    with pytest.raises(NoMinutes):
        charge(db, user, 6, "asset:2")
    db.close()


def test_admin_settings_signup_toggle(client):
    owner = make_user("owner@x.com", Role.owner, totp=True)
    assert client.get("/auth/config").json()["public_signup"] is False
    login(client, owner.email, owner.totp_secret)
    r = client.put("/admin/settings", json={"public_signup": True, "costs": {"gpu_server": 300, "nope": 1}})
    assert r.json()["public_signup"] is True and r.json()["costs"]["gpu_server"] == 300
    assert "nope" not in r.json()["costs"]
    client.cookies.clear()
    r = client.post("/auth/register", json={"email": "pub@x.com", "password": "supersecret123"})
    assert r.status_code == 201


def test_plan_limits_live_and_jobs(client):
    u = make_user("a@x.com", plan="free")
    login(client, u.email)
    r = client.post("/lives", json={"url": "https://www.twitch.tv/someone"})
    assert r.json()["detail"] == "plan_no_live"


def test_plans_editor_owner_only(client):
    owner = make_user("owner@x.com", Role.owner, totp=True)
    login(client, owner.email, owner.totp_secret)
    body = {"name": "Agencia", "price_label": "US$99/mes", "price_cents": 9900, "monthly_minutes": 6000,
            "live": True, "max_active_jobs": 10, "sort": 3}
    assert client.put("/admin/plans/agencia", json=body).status_code == 200
    assert client.put("/admin/plans/Bad Id", json=body).status_code in (400, 404)
    ids = [p["id"] for p in client.get("/admin/plans").json()["plans"]]
    assert ids[-1] == "agencia"

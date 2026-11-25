from app.models import Role
from tests.conftest import PASSWORD, login, make_user


def owner_client(client):
    owner = make_user("owner@x.com", Role.owner, totp=True)
    login(client, owner.email, owner.totp_secret)
    return owner


def test_public_signup_disabled(client):
    r = client.post("/auth/register", json={"email": "a@x.com", "password": PASSWORD})
    assert r.status_code == 403 and r.json()["detail"] == "invitation_required"


def test_invitation_flow_single_use(client):
    owner_client(client)
    r = client.post("/admin/invitations", json={"email": "new@x.com"})
    assert r.status_code == 201
    token = r.json()["link"].split("token=")[1]
    client.post("/auth/logout")
    client.cookies.clear()

    assert client.get(f"/auth/invitations/{token}").json()["email"] == "new@x.com"
    bad = client.post("/auth/register", json={"email": "other@x.com", "password": PASSWORD,
                                              "invitation_token": token})
    assert bad.json()["detail"] == "invitation_email_mismatch"
    ok = client.post("/auth/register", json={"email": "new@x.com", "password": PASSWORD,
                                             "invitation_token": token})
    assert ok.status_code == 201 and ok.json()["role"] == "user"
    assert client.get("/auth/me").json()["email"] == "new@x.com"
    again = client.post("/auth/register", json={"email": "new@x.com", "password": PASSWORD,
                                                "invitation_token": token})
    assert again.json()["detail"] == "invitation_invalid"


def test_login_rate_limited(client):
    make_user("u@x.com")
    for _ in range(5):
        assert client.post("/auth/login", json={"email": "u@x.com", "password": "wrong-pass"}).status_code == 401
    r = client.post("/auth/login", json={"email": "u@x.com", "password": PASSWORD})
    assert r.status_code == 429


def test_totp_required_on_login(client):
    u = make_user("t@x.com", totp=True)
    r = client.post("/auth/login", json={"email": u.email, "password": PASSWORD})
    assert r.json()["detail"] == "totp_required"
    login(client, u.email, u.totp_secret)


def test_admin_requires_2fa(client):
    make_user("adm@x.com", Role.admin)
    login(client, "adm@x.com")
    assert client.get("/admin/users").json()["detail"] == "2fa_required"


def test_user_cannot_access_admin(client):
    make_user("u@x.com")
    login(client, "u@x.com")
    assert client.get("/admin/users").status_code == 403


def test_owner_is_protected(client):
    owner = make_user("owner@x.com", Role.owner, totp=True)
    adm = make_user("adm@x.com", Role.admin, totp=True)
    login(client, adm.email, adm.totp_secret)
    r = client.patch(f"/admin/users/{owner.id}", json={"is_active": False})
    assert r.status_code == 403 and r.json()["detail"] == "owner_protected"
    # admins cannot invite admins, nobody can invite an owner
    assert client.post("/admin/invitations", json={"email": "z@x.com", "role": "admin"}).status_code == 403
    assert client.post("/admin/invitations", json={"email": "z@x.com", "role": "owner"}).status_code == 400


def test_owner_can_pause_user(client):
    owner_client(client)
    u = make_user("u@x.com")
    r = client.patch(f"/admin/users/{u.id}", json={"is_active": False})
    assert r.json()["is_active"] is False
    client.cookies.clear()
    r = client.post("/auth/login", json={"email": "u@x.com", "password": PASSWORD})
    assert r.status_code == 401


def test_2fa_setup_and_enable(client):
    import pyotp
    make_user("adm@x.com", Role.admin)
    login(client, "adm@x.com")
    secret = client.post("/auth/2fa/setup").json()["secret"]
    assert client.post("/auth/2fa/enable", json={"code": "000000"}).status_code == 400
    r = client.post("/auth/2fa/enable", json={"code": pyotp.TOTP(secret).now()})
    assert r.json()["totp_enabled"] is True
    assert client.get("/admin/users").status_code == 200

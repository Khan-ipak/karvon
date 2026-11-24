"""Login, roles and the audit log (#10)."""

import base64

import pytest

from karvon import auth
from karvon.models import Role
from tests.conftest import ADMIN_PASSWORD, gate_in, login


def _add_user(client, username, role, password="secret-pw-1"):
    r = client.post(
        "/users",
        data={"username": username, "password": password, "role": role},
        follow_redirects=False,
    )
    assert r.status_code == 303 and "error" not in r.headers["location"], r.headers
    return password


def _as(client, username, password):
    client.post("/logout")
    return login(client, username, password)


def test_password_hash_roundtrip():
    stored = auth.hash_password("correct horse")
    assert stored.startswith("scrypt$")
    assert auth.verify_password("correct horse", stored)
    assert not auth.verify_password("wrong", stored)
    assert not auth.verify_password("x", "garbage")


def test_api_needs_login(anon_client):
    r = anon_client.get("/api/v1/visits")
    assert r.status_code == 401
    assert r.json()["code"] == "not_authenticated"
    assert r.headers["www-authenticate"].startswith("Basic")


def test_pages_redirect_to_login(anon_client):
    r = anon_client.get("/yard?x=1", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login?next=/yard%3Fx%3D1"
    assert anon_client.get("/login").status_code == 200
    assert anon_client.get("/healthz").status_code == 200


def test_http_basic_auth(anon_client):
    token = base64.b64encode(f"admin:{ADMIN_PASSWORD}".encode()).decode()
    r = anon_client.get("/api/v1/me", headers={"Authorization": f"Basic {token}"})
    assert r.status_code == 200
    assert r.json()["role"] == "admin"

    bad = base64.b64encode(b"admin:nope").decode()
    r = anon_client.get("/api/v1/me", headers={"Authorization": f"Basic {bad}"})
    assert r.status_code == 401


def test_wrong_password_shows_error(anon_client):
    r = anon_client.post("/login", data={"username": "admin", "password": "nope"})
    assert r.status_code == 200
    assert "Wrong username or password" in r.text
    assert auth.SESSION_COOKIE not in anon_client.cookies


@pytest.mark.parametrize("target", ["https://evil.example", "//evil.example", "/\\evil"])
def test_login_does_not_redirect_off_site(anon_client, target):
    r = anon_client.post(
        "/login",
        data={"username": "admin", "password": ADMIN_PASSWORD, "next": target},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/"


def test_login_returns_to_next(anon_client):
    r = anon_client.post(
        "/login",
        data={"username": "admin", "password": ADMIN_PASSWORD, "next": "/stock"},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/stock"


def test_logout_ends_session(client):
    token = client.cookies[auth.SESSION_COOKIE]
    client.post("/logout")
    client.cookies.set(auth.SESSION_COOKIE, token)  # a stolen copy no longer works
    assert client.get("/api/v1/me").status_code == 401


def test_roles_limit_api_actions(client):
    pw = _add_user(client, "acc", "accountant")
    _add_user(client, "broker", "customs")
    _add_user(client, "store", "warehouse")
    visit = gate_in(client)
    vid = visit["id"]
    client.post(f"/api/v1/visits/{vid}/park", json={})
    op = client.post(
        f"/api/v1/visits/{vid}/operations",
        json={"type": "warehouse_unload", "weight_kg": "1000", "cargo_owner": "X"},
    ).json()

    _as(client, "acc", pw)
    assert client.get("/api/v1/visits").status_code == 200  # everyone can look
    r = client.post(
        "/api/v1/visits", json={"plate_number": "X", "driver_name": "A", "driver_passport": "P"}
    )
    assert r.status_code == 403
    assert r.json()["code"] == "forbidden"
    assert client.post(f"/api/v1/operations/{op['id']}/start").status_code == 403

    _as(client, "store", pw)
    assert client.post(f"/api/v1/operations/{op['id']}/start").status_code == 200
    assert client.post(f"/api/v1/operations/{op['id']}/complete").status_code == 200
    r = client.post(f"/api/v1/operations/{op['id']}/customs", json={"cleared": True})
    assert r.status_code == 403

    _as(client, "broker", pw)
    r = client.post(f"/api/v1/operations/{op['id']}/customs", json={"cleared": True})
    assert r.status_code == 200


def test_web_action_without_permission_shows_error(client):
    vid = gate_in(client)["id"]
    pw = _add_user(client, "acc", "accountant")
    _as(client, "acc", pw)

    page = client.get(f"/visits/{vid}").text
    assert "/action/park" not in page  # the button is hidden
    r = client.post(f"/visits/{vid}/action/park", follow_redirects=False)
    assert "error=" in r.headers["location"]
    assert client.get(f"/api/v1/visits/{vid}").json()["status"] == "registered"


def test_history_records_who_did_what(client):
    pw = _add_user(client, "yardman", "yard")
    vid = gate_in(client)["id"]
    _as(client, "yardman", pw)
    client.post(f"/api/v1/visits/{vid}/park", json={})

    events = client.get(f"/api/v1/visits/{vid}/history").json()
    assert [(e["username"], e["action"]) for e in events] == [
        ("admin", "gate_in"),
        ("yardman", "park"),
    ]
    assert events[1]["details"] == "A-01"
    page = client.get(f"/visits/{vid}").text
    assert "yardman" in page and "Parked" in page


def test_only_admin_manages_users(client):
    pw = _add_user(client, "op", "operator")
    _as(client, "op", pw)
    r = client.get("/users", follow_redirects=False)
    assert r.status_code == 303 and "error=" in r.headers["location"]
    r = client.post(
        "/users",
        data={"username": "x", "password": "12345678", "role": "admin"},
        follow_redirects=False,
    )
    assert "error=" in r.headers["location"]


def test_deactivated_user_is_logged_out(client):
    pw = _add_user(client, "temp", "operator")
    _as(client, "temp", pw)
    me = client.get("/api/v1/me").json()
    token = client.cookies[auth.SESSION_COOKIE]

    _as(client, "admin", ADMIN_PASSWORD)
    client.post(f"/users/{me['id']}/deactivate")

    client.cookies.set(auth.SESSION_COOKIE, token)
    assert client.get("/api/v1/me").status_code == 401
    r = client.post("/login", data={"username": "temp", "password": pw})
    assert "Wrong username or password" in r.text


def test_short_password_rejected(client):
    r = client.post(
        "/users",
        data={"username": "x", "password": "short", "role": "yard"},
        follow_redirects=False,
    )
    assert "error=" in r.headers["location"]


def test_demo_has_a_user_per_role(demo_client):
    page = demo_client.get("/login").text
    for role in Role:
        assert f"<code>{role.value}</code>" in page
    visit_page = demo_client.get("/visits/2").text
    assert "system" in visit_page  # demo history was written by the seed


def test_users_cli(tmp_path, monkeypatch, capsys):
    from karvon import users

    monkeypatch.setenv("KARVON_DATABASE_URL", f"sqlite:///{tmp_path / 'cli.db'}")
    monkeypatch.setattr(users.getpass, "getpass", lambda prompt="": "cli-password")
    assert users.main(["create", "Boss", "--role", "admin", "--name", "The Boss"]) == 0
    assert users.main(["create", "boss", "--role", "yard"]) == 1  # names are case-insensitive
    assert users.main(["password", "boss"]) == 0
    assert users.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "boss" in out and "The Boss" in out


def test_visit_page_says_when_role_has_no_actions(client):
    vid = gate_in(client)["id"]
    pw = _add_user(client, "broker", "customs")
    _as(client, "broker", pw)
    assert "No actions available right now." in client.get(f"/visits/{vid}").text


def test_upgraded_database_gets_no_demo_users(tmp_path, caplog):
    """A v0.1.0 database with real trucks must not get password = username accounts."""
    from fastapi.testclient import TestClient

    from karvon.config import Settings
    from karvon.db import make_engine, make_sessionmaker
    from karvon.main import create_app
    from karvon.migrate import upgrade
    from karvon.seed import seed_reference_data
    from karvon.services import GateInData
    from karvon.services import gate_in as service_gate_in

    url = f"sqlite:///{tmp_path / 'old.db'}"
    engine = make_engine(url)
    upgrade(engine)
    with make_sessionmaker(engine)() as s, s.begin():
        seed_reference_data(s)
        service_gate_in(s, GateInData("01R000RR", "Real Driver", "R1"))

    with TestClient(create_app(Settings(database_url=url, seed_demo=True))) as c:
        r = c.post("/login", data={"username": "admin", "password": "admin"})
        assert "Wrong username or password" in r.text
        assert "Demo users" not in c.get("/login").text
    assert "No users yet" in caplog.text


def test_public_demo_locks_users(tmp_path):
    from fastapi.testclient import TestClient

    from karvon.config import Settings
    from karvon.main import create_app

    settings = Settings(database_url=f"sqlite:///{tmp_path / 'pub.db'}", public_demo=True)
    with TestClient(create_app(settings)) as c:
        login(c, "admin", "admin")
        assert "Public demo" in c.get("/").text
        r = c.post("/users/1/password", data={"password": "hijacked-1"}, follow_redirects=False)
        assert "error=" in r.headers["location"]
        r = c.post(
            "/users",
            data={"username": "x", "password": "12345678", "role": "admin"},
            follow_redirects=False,
        )
        assert "error=" in r.headers["location"]
        c.post("/logout")
        login(c, "admin", "admin")  # still the demo password

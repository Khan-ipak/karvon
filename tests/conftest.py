import pytest
from fastapi.testclient import TestClient

from karvon.config import Settings
from karvon.main import create_app

ADMIN_PASSWORD = "test-admin-pw"


def login(client, username, password):
    r = client.post(
        "/login", data={"username": username, "password": password}, follow_redirects=False
    )
    assert r.status_code == 303, r.text
    return client


@pytest.fixture
def anon_client(tmp_path):
    """Not logged in. The database has one admin (`admin` / ADMIN_PASSWORD)."""
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        seed_demo=False,
        admin_password=ADMIN_PASSWORD,
    )
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture
def client(anon_client):
    """Logged in as admin, who may do everything."""
    return login(anon_client, "admin", ADMIN_PASSWORD)


@pytest.fixture
def demo_client(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'demo.db'}", seed_demo=True)
    with TestClient(create_app(settings)) as c:
        yield login(c, "admin", "admin")


def gate_in(client, plate="01A123BC", passport="AA0000001", **extra):
    body = {"plate_number": plate, "driver_name": "Test Driver", "driver_passport": passport}
    r = client.post("/api/v1/visits", json=body | extra)
    assert r.status_code == 201, r.text
    return r.json()

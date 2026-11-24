"""KARVON_TIMEZONE: times are shown and days are counted in the terminal's zone."""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from karvon import reports
from karvon.config import Settings
from karvon.main import create_app
from tests.conftest import ADMIN_PASSWORD, gate_in, login
from tests.test_reports import finished_visit, move

TASHKENT = ZoneInfo("Asia/Tashkent")  # UTC+5, no daylight saving


@pytest.fixture
def tashkent_client(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'tz.db'}",
        seed_demo=False,
        admin_password=ADMIN_PASSWORD,
        timezone="Asia/Tashkent",
    )
    with TestClient(create_app(settings)) as c:
        yield login(c, "admin", ADMIN_PASSWORD)


def test_late_evening_utc_is_next_day_in_tashkent(tashkent_client):
    vid = finished_visit(tashkent_client, "01T000TT", "T1")
    arrived = datetime(2026, 11, 10, 20, 30, tzinfo=UTC)  # 01:30 on the 11th in Tashkent
    move(tashkent_client, vid, arrived, datetime(2026, 11, 11, 2, tzinfo=UTC))

    with tashkent_client.app.state.sessionmaker() as s:
        local = reports.build(s, date(2026, 11, 10), date(2026, 11, 11), TASHKENT)
        utc = reports.build(s, date(2026, 11, 10), date(2026, 11, 11))
    assert [d.arrivals for d in local.days] == [0, 1]
    assert [d.arrivals for d in utc.days] == [1, 0]

    r = tashkent_client.get(
        "/api/v1/reports", params={"from": "2026-11-11", "to": "2026-11-11"}
    ).json()
    assert r["arrivals"] == 1 and r["exits"] == 1


def test_pages_show_local_time(tashkent_client):
    vid = gate_in(tashkent_client)["id"]
    move(tashkent_client, vid, datetime(2026, 11, 10, 20, 30, tzinfo=UTC))
    assert "2026-11-11 01:30" in tashkent_client.get(f"/visits/{vid}").text
    assert "2026-11-11 01:30" in tashkent_client.get("/").text
    assert "Asia/Tashkent" in tashkent_client.get("/reports").text


def test_api_stays_in_utc(tashkent_client):
    vid = gate_in(tashkent_client)["id"]
    move(tashkent_client, vid, datetime(2026, 11, 10, 20, 30, tzinfo=UTC))
    arrived = tashkent_client.get(f"/api/v1/visits/{vid}").json()["arrived_at"]
    assert arrived.startswith("2026-11-10T20:30")


def test_unknown_time_zone_stops_startup(tmp_path):
    with pytest.raises(SystemExit, match="Mars/Olympus"):
        create_app(Settings(database_url=f"sqlite:///{tmp_path / 'x.db'}", timezone="Mars/Olympus"))

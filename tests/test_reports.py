"""Reports: throughput, dwell time and revenue for a period."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import update

from karvon import reports
from karvon.models import Charge, Operation, Visit
from karvon.services import DomainError
from tests.conftest import gate_in

API = "/api/v1"


def post(client, path, json=None):
    r = client.post(API + path, json=json or {})
    assert r.status_code in (200, 201), r.text
    return r.json()


def finished_visit(client, plate, passport, weight="2000"):
    """Gate in, unload to the warehouse, pay and leave."""
    vid = gate_in(client, plate=plate, passport=passport)["id"]
    post(client, f"/visits/{vid}/park")
    op = post(
        client,
        f"/visits/{vid}/operations",
        {"type": "warehouse_unload", "weight_kg": weight, "cargo_owner": "Owner"},
    )
    post(client, f"/operations/{op['id']}/start")
    post(client, f"/operations/{op['id']}/complete")
    post(client, f"/operations/{op['id']}/customs", {"cleared": True})
    post(client, f"/visits/{vid}/request-exit")
    post(client, f"/visits/{vid}/pay")
    post(client, f"/visits/{vid}/gate-out")
    return vid


def move(client, vid, arrived, exited=None):
    """Put a visit (and everything that happened on it) at fixed times."""
    with client.app.state.sessionmaker() as s, s.begin():
        s.execute(update(Visit).where(Visit.id == vid).values(arrived_at=arrived, exited_at=exited))
        when = exited or arrived
        s.execute(update(Charge).where(Charge.visit_id == vid).values(created_at=when))
        s.execute(update(Operation).where(Operation.visit_id == vid).values(completed_at=when))


def test_period_defaults_and_limits():
    today = date(2026, 11, 23)
    assert reports.period(None, None, today) == (date(2026, 10, 25), today)
    assert reports.period(date(2026, 11, 1), None, today) == (date(2026, 11, 1), today)
    with pytest.raises(DomainError, match="after"):
        reports.period(date(2026, 11, 2), date(2026, 11, 1))
    with pytest.raises(DomainError):
        reports.period(date(2024, 1, 1), date(2026, 1, 1))


def test_report_counts_dwell_operations_and_revenue(client):
    day = datetime(2026, 11, 10, 8, tzinfo=UTC)
    a = finished_visit(client, "01A001AA", "P1", weight="2000")
    b = finished_visit(client, "01A002AA", "P2", weight="3000")
    c = gate_in(client, plate="01A003AA", passport="P3")["id"]  # still on site
    move(client, a, day, day + timedelta(hours=10))
    move(client, b, day, day + timedelta(days=1, hours=20))  # 44 h, leaves on the 12th
    move(client, c, day + timedelta(days=1))
    outside = finished_visit(client, "01A004AA", "P4")
    move(client, outside, day - timedelta(days=30), day - timedelta(days=29))

    r = client.get(f"{API}/reports", params={"from": "2026-11-10", "to": "2026-11-12"})
    assert r.status_code == 200, r.text
    rep = r.json()

    assert (rep["arrivals"], rep["exits"]) == (3, 2)
    assert rep["dwell"] == {
        "count": 2,
        "average_hours": 27.0,
        "median_hours": 27.0,
        "max_hours": 44.0,
    }
    unload = next(o for o in rep["operations"] if o["type"] == "warehouse_unload")
    assert unload["count"] == 2 and unload["tonnes"] == "5.000"

    # 5 t unload at 5/t = 25; parking was charged at request-exit, 1 day each = 30
    by_code = {row["tariff_code"]: row for row in rep["revenue"]}
    assert by_code["warehouse_unload"]["amount"] == "25.00"
    assert by_code["parking_day"]["amount"] == "30.00"
    assert rep["revenue_total"] == rep["paid_total"] == "55.00"
    assert by_code["parking_day"]["unpaid"] == "0.00"

    days = {d["day"]: d for d in rep["days"]}
    assert list(days) == ["2026-11-10", "2026-11-11", "2026-11-12"]
    assert (days["2026-11-10"]["arrivals"], days["2026-11-10"]["exits"]) == (2, 1)
    assert (days["2026-11-11"]["arrivals"], days["2026-11-11"]["exits"]) == (1, 0)
    assert (days["2026-11-12"]["arrivals"], days["2026-11-12"]["exits"]) == (0, 1)
    assert days["2026-11-12"]["revenue"] == "30.00"  # visit b: 15 unload + 15 parking


def test_empty_period(client):
    rep = client.get(f"{API}/reports", params={"from": "2020-01-01", "to": "2020-01-31"}).json()
    assert rep["arrivals"] == 0 and rep["dwell"] is None and rep["revenue"] == []
    assert len(rep["days"]) == 31


def test_bad_period_is_rejected(client):
    r = client.get(f"{API}/reports", params={"from": "2026-11-02", "to": "2026-11-01"})
    assert r.status_code == 409 and r.json()["code"] == "bad_period"


def test_reports_need_permission(client):
    client.post("/users", data={"username": "y", "password": "yard-pass-1", "role": "yard"})
    client.post("/logout")
    client.post("/login", data={"username": "y", "password": "yard-pass-1"})
    assert client.get(f"{API}/reports").status_code == 403
    r = client.get("/reports", follow_redirects=False)
    assert r.status_code == 303 and "error=" in r.headers["location"]
    assert "/reports" not in client.get("/").text


@pytest.mark.parametrize("query", ["", "?start=2026-11-01&end=2026-11-23", "?start=oops"])
def test_reports_page(demo_client, query):
    r = demo_client.get("/reports" + query)
    assert r.status_code == 200
    assert "Completed operations" in r.text
    if query == "?start=oops":
        assert "Not a date" in r.text

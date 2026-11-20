"""CSV export of visits (issue #8): header row, one data row, date filters."""

import csv
import io
from datetime import UTC, datetime, timedelta

from tests.conftest import gate_in

API = "/api/v1"

EXPECTED_HEADER = [
    "id",
    "plate",
    "country",
    "driver",
    "status",
    "slot",
    "arrived_at",
    "exited_at",
    "total charged",
    "total paid",
]


def post(client, path, json=None, status=200):
    r = client.post(API + path, json=json or {})
    assert r.status_code == status, r.text
    return r.json()


def run_full_cycle(client, plate, passport):
    """Gate a visit in, run it through a direct transfer, weigh it, pay it.

    Mirrors test_direct_transfer_full_cycle: charges are direct_transfer
    48.00 + weighbridge 10.00 + parking_day 15.00 = 73.00, all paid.
    Returns (paid visit id, receiver visit id).
    """
    visit = gate_in(client, plate, passport, country="pk")
    post(client, f"/visits/{visit['id']}/park")
    receiver = gate_in(client, "30B456CD", "UZ1")
    op = post(
        client,
        f"/visits/{visit['id']}/operations",
        {"type": "direct_transfer", "weight_kg": "12000", "target_visit_id": receiver["id"]},
        status=201,
    )
    post(client, f"/operations/{op['id']}/customs")
    post(client, f"/operations/{op['id']}/start")
    post(client, f"/operations/{op['id']}/complete")
    post(client, f"/visits/{visit['id']}/weigh", {"gross_kg": "15000", "tare_kg": "14800"}, 201)
    post(client, f"/visits/{visit['id']}/request-exit")
    assert post(client, f"/visits/{visit['id']}/pay")["status"] == "cleared"
    return visit["id"], receiver["id"]


def fetch_csv(client, params=None):
    r = client.get(f"{API}/visits.csv", params=params)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    assert r.headers["content-disposition"] == 'attachment; filename="visits.csv"'
    # utf-8-sig strips the BOM, the way Excel reads the file
    return list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))


def test_csv_export_header_and_data_row(client):
    paid_id, receiver_id = run_full_cycle(client, "LHR-8830", "PK1")
    plain = gate_in(client, "01A123BC", "AA0000001")

    rows = fetch_csv(client)
    assert rows[0] == EXPECTED_HEADER
    by_id = {int(row[0]): row for row in rows[1:]}
    assert set(by_id) == {paid_id, receiver_id, plain["id"]}

    paid = by_id[paid_id]
    assert paid[1] == "LHR-8830" and paid[2] == "PK"
    assert paid[3] == "Test Driver" and paid[4] == "cleared"
    assert paid[5] == "A-01"  # cleared, not yet gated out, so still parked
    assert paid[6] and paid[7] == ""  # arrived, not exited yet
    assert paid[8] == "73.00" and paid[9] == "73.00"

    assert by_id[plain["id"]][8] == "0.00" and by_id[plain["id"]][9] == "0.00"
    assert by_id[receiver_id][8] == "0.00" and by_id[receiver_id][9] == "0.00"


def test_csv_export_date_filter(client):
    gate_in(client, "01A123BC", "AA0000001")
    now = datetime.now(UTC)

    future = (now + timedelta(days=1)).isoformat()
    assert fetch_csv(client, {"from": future}) == [EXPECTED_HEADER]

    past = (now - timedelta(days=1)).isoformat()
    assert len(fetch_csv(client, {"from": past})) == 2
    assert fetch_csv(client, {"to": past}) == [EXPECTED_HEADER]

    # a window that contains the visit returns it
    assert len(fetch_csv(client, {"from": past, "to": future})) == 2


def test_csv_export_bom_and_cyrillic_driver(client):
    gate_in(client, "01A123BC", "AA0000001", driver_name="Азиз Абдуллаев")

    r = client.get(f"{API}/visits.csv")
    assert r.status_code == 200, r.text
    # a BOM + an explicit charset keep Excel from mangling Cyrillic names
    assert r.content.startswith(b"\xef\xbb\xbf")
    assert "charset=utf-8" in r.headers["content-type"]

    rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert rows[0] == EXPECTED_HEADER
    assert rows[1][3] == "Азиз Абдуллаев"


def test_csv_export_formula_injection_is_escaped(client):
    gate_in(client, "01A123BC", "AA0000001", driver_name='=HYPERLINK("http://x","y")')
    gate_in(client, "30B456CD", "UZ1", driver_name="@SUM(1+1)")

    rows = fetch_csv(client)
    drivers = {row[3] for row in rows[1:]}
    # a leading ' keeps the cell a literal string in a spreadsheet
    assert drivers == {'\'=HYPERLINK("http://x","y")', "'@SUM(1+1)"}


def test_csv_export_date_only_to_means_end_of_day(client):
    gate_in(client, "01A123BC", "AA0000001")
    today = datetime.now(UTC).date()

    # the visit arrived today, so a date-only `to` of today includes it
    assert len(fetch_csv(client, {"to": today.isoformat()})) == 2
    # ...while yesterday's date still excludes it
    yesterday = (today - timedelta(days=1)).isoformat()
    assert fetch_csv(client, {"to": yesterday}) == [EXPECTED_HEADER]


def test_dashboard_links_csv_export(demo_client):
    page = demo_client.get("/")
    assert page.status_code == 200
    assert "/api/v1/visits.csv" in page.text
    assert "Download CSV" in page.text

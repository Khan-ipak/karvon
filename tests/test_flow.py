"""End-to-end visit flows through the REST API, mirroring the terminal's business process."""

from decimal import Decimal

from tests.conftest import gate_in

API = "/api/v1"


def post(client, path, json=None, status=200):
    r = client.post(API + path, json=json or {})
    assert r.status_code == status, r.text
    return r.json()


def test_direct_transfer_full_cycle(client):
    sender = gate_in(client, "LHR-8830", "PK1", country="pk")
    receiver = gate_in(client, "30B456CD", "UZ1")
    assert sender["status"] == "registered"
    assert sender["vehicle"]["country"] == "PK"

    assert post(client, f"/visits/{sender['id']}/park")["status"] == "parked"
    assert post(client, f"/visits/{receiver['id']}/park")["slot"]["label"] == "A-02"

    op = post(
        client,
        f"/visits/{sender['id']}/operations",
        {"type": "direct_transfer", "weight_kg": "12000", "target_visit_id": receiver["id"]},
        status=201,
    )
    # customs may be done before the physical work
    post(client, f"/operations/{op['id']}/customs")
    post(client, f"/operations/{op['id']}/start")
    assert client.get(f"{API}/visits/{sender['id']}").json()["status"] == "in_operation"
    done = post(client, f"/operations/{op['id']}/complete")
    assert done["status"] == "completed"

    post(client, f"/visits/{sender['id']}/weigh", {"gross_kg": "15000", "tare_kg": "14800"}, 201)

    visit = post(client, f"/visits/{sender['id']}/request-exit")
    assert visit["status"] == "awaiting_payment"
    codes = {c["tariff_code"]: Decimal(c["amount"]) for c in visit["charges"]}
    assert codes == {
        "direct_transfer": Decimal("48.00"),  # 12 t * 4.00
        "weighbridge": Decimal("10.00"),
        "parking_day": Decimal("15.00"),  # same day counts as one day
    }
    assert Decimal(visit["total_due"]) == Decimal("73.00")

    r = client.post(f"{API}/visits/{sender['id']}/gate-out")
    assert r.status_code == 409 and r.json()["code"] == "not_cleared"

    assert post(client, f"/visits/{sender['id']}/pay")["status"] == "cleared"
    out = post(client, f"/visits/{sender['id']}/gate-out")
    assert out["status"] == "exited" and out["slot"] is None

    # the slot is free again and the same truck can come back
    free_again = post(client, f"/visits/{gate_in(client, 'NEW1', 'N1')['id']}/park")
    assert free_again["slot"]["label"] == "A-01"
    again = gate_in(client, "LHR-8830", "PK1")
    assert again["id"] != sender["id"]


def test_warehouse_unload_then_load(client):
    inbound = gate_in(client, "KBL-1", "AF1")
    post(client, f"/visits/{inbound['id']}/park")
    op = post(
        client,
        f"/visits/{inbound['id']}/operations",
        {"type": "warehouse_unload", "weight_kg": "18000", "cargo_owner": "Fruit LLC"},
        status=201,
    )
    post(client, f"/operations/{op['id']}/start")
    done = post(client, f"/operations/{op['id']}/complete")
    lot_id = done["stock_lot_id"]
    assert lot_id is not None
    [lot] = client.get(f"{API}/stock").json()
    assert Decimal(lot["quantity_kg"]) == Decimal("18000")

    outbound = gate_in(client, "30X1", "UZ2")
    post(client, f"/visits/{outbound['id']}/park")

    r = client.post(
        f"{API}/visits/{outbound['id']}/operations",
        json={"type": "warehouse_load", "weight_kg": "20000", "stock_lot_id": lot_id},
    )
    assert r.status_code == 409 and r.json()["code"] == "insufficient_stock"

    load = post(
        client,
        f"/visits/{outbound['id']}/operations",
        {"type": "warehouse_load", "weight_kg": "8000", "stock_lot_id": lot_id},
        status=201,
    )
    post(client, f"/operations/{load['id']}/start")
    post(client, f"/operations/{load['id']}/complete")
    [lot] = client.get(f"{API}/stock").json()
    assert Decimal(lot["quantity_kg"]) == Decimal("10000")


def test_exit_blocked_until_customs_and_operations_done(client):
    v = gate_in(client)
    post(client, f"/visits/{v['id']}/park")
    op = post(
        client,
        f"/visits/{v['id']}/operations",
        {"type": "warehouse_unload", "weight_kg": "1000", "cargo_owner": "X"},
        status=201,
    )
    r = client.post(f"{API}/visits/{v['id']}/request-exit")
    assert r.json()["code"] == "open_operation"

    post(client, f"/operations/{op['id']}/start")
    post(client, f"/operations/{op['id']}/complete")
    r = client.post(f"{API}/visits/{v['id']}/request-exit")
    assert r.json()["code"] == "customs_pending"

    # customs after the physical work is allowed too
    post(client, f"/operations/{op['id']}/customs")
    assert post(client, f"/visits/{v['id']}/request-exit")["status"] == "awaiting_payment"


def test_rules(client):
    v = gate_in(client)
    r = client.post(
        f"{API}/visits",
        json={
            "plate_number": "01a123bc",
            "driver_name": "X",
            "driver_passport": "Y",
        },
    )
    assert r.status_code == 409 and r.json()["code"] == "already_on_site"

    r = client.post(
        f"{API}/visits/{v['id']}/operations", json={"type": "direct_transfer", "weight_kg": "5"}
    )
    assert r.json()["code"] == "target_required"

    op = post(
        client,
        f"/visits/{v['id']}/operations",
        {"type": "warehouse_unload", "weight_kg": "5", "cargo_owner": "X"},
        status=201,
    )
    r = client.post(f"{API}/operations/{op['id']}/start")
    assert r.json()["code"] == "not_parked"

    r = client.post(f"{API}/visits/{v['id']}/weigh", json={"gross_kg": "100", "tare_kg": "200"})
    assert r.json()["code"] == "invalid_weight"

    assert client.get(f"{API}/visits/999").status_code == 404


def test_slot_cannot_be_double_booked(client):
    a, b = gate_in(client, "A1", "P1"), gate_in(client, "B1", "P2")
    slot = post(client, f"/visits/{a['id']}/park")["slot"]["id"]
    r = client.post(f"{API}/visits/{b['id']}/park", json={"slot_id": slot})
    assert r.status_code == 409 and r.json()["code"] == "slot_taken"


def test_search_by_plate(client):
    gate_in(client, "KBL-4521", "P1")
    gate_in(client, "LHR-8830", "P2")
    gate_in(client, "01A_777", "P3")

    def plates(**params):
        r = client.get(f"{API}/visits", params=params)
        return sorted(v["vehicle"]["plate_number"] for v in r.json())

    assert plates(plate="kbl") == ["KBL-4521"]
    assert plates(plate=" 88 ") == ["LHR-8830"]
    assert plates(plate="_") == ["01A_777"]  # underscore is literal, not a wildcard
    assert plates(plate="zzz") == []
    assert len(plates()) == 3

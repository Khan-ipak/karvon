"""Yard map: occupancy, waiting vehicles, parking by clicking a free slot."""

from tests.conftest import gate_in

API = "/api/v1"


def slots(client):
    return {s["label"]: s for z in client.get(f"{API}/yard").json() for s in z["slots"]}


def test_api_shows_who_is_in_each_slot(client):
    vid = gate_in(client, plate="01A111AA")["id"]
    client.post(f"{API}/visits/{vid}/park", json={})
    by_label = slots(client)
    assert by_label["A-01"]["visit"]["vehicle"]["plate_number"] == "01A111AA"
    assert by_label["A-01"]["visit"]["status"] == "parked"
    assert by_label["A-02"]["visit"] is None


def test_map_shows_occupancy_and_waiting_vehicles(client):
    gate_in(client, plate="01W000WW", passport="W1")  # at the gate, no slot
    parked = gate_in(client, plate="01P000PP", passport="P1")["id"]
    client.post(f"{API}/visits/{parked}/park", json={})

    page = client.get("/yard").text
    assert "1 / 30" in page
    assert "Waiting for a slot (1)" in page
    assert 'href="/visits/1">01W000WW' in page
    assert "01P000PP" in page and "tile parked" in page


def test_park_by_clicking_a_free_slot(client):
    waiting = gate_in(client, plate="01W000WW", passport="W1")["id"]
    target = slots(client)["B-03"]["id"]

    page = client.get(f"/yard?slot={target}").text
    assert "Park in slot <strong>B-03</strong>" in page
    assert f'<option value="{waiting}">01W000WW</option>' in page

    r = client.post(
        "/yard/park", data={"visit_id": waiting, "slot_id": target}, follow_redirects=False
    )
    assert r.headers["location"] == "/yard"
    assert slots(client)["B-03"]["visit"]["id"] == waiting


def test_move_a_parked_vehicle(client):
    vid = gate_in(client)["id"]
    client.post(f"{API}/visits/{vid}/park", json={})  # A-01
    target = slots(client)["W-02"]["id"]
    assert "(move from A-01)" in client.get(f"/yard?slot={target}").text
    client.post("/yard/park", data={"visit_id": vid, "slot_id": target})
    by_label = slots(client)
    assert by_label["W-02"]["visit"]["id"] == vid and by_label["A-01"]["visit"] is None


def test_occupied_slot_cannot_be_picked(client):
    a = gate_in(client, plate="01A111AA", passport="A1")["id"]
    b = gate_in(client, plate="01B222BB", passport="B1")["id"]
    client.post(f"{API}/visits/{a}/park", json={})
    taken = slots(client)["A-01"]["id"]
    assert 'id="park"' not in client.get(f"/yard?slot={taken}").text

    r = client.post("/yard/park", data={"visit_id": b, "slot_id": taken}, follow_redirects=False)
    assert r.headers["location"].startswith(f"/yard?slot={taken}&error=")
    assert slots(client)["A-01"]["visit"]["id"] == a


def test_search_highlights_the_vehicle(client):
    for i, plate in enumerate(["01A111AA", "KBL-4521"]):
        vid = gate_in(client, plate=plate, passport=f"P{i}")["id"]
        client.post(f"{API}/visits/{vid}/park", json={})
    page = client.get("/yard?q=kbl").text
    assert page.count("match") == 2  # the CSS rule + one tile
    assert "tile parked match" in page


def test_roles_without_park_see_a_read_only_map(client):
    vid = gate_in(client)["id"]
    client.post("/users", data={"username": "acc", "password": "acc-pass-1", "role": "accountant"})
    client.post("/logout")
    client.post("/login", data={"username": "acc", "password": "acc-pass-1"})

    page = client.get("/yard").text
    assert "/yard?slot=" not in page
    target = slots(client)["A-05"]["id"]
    r = client.post("/yard/park", data={"visit_id": vid, "slot_id": target}, follow_redirects=False)
    assert "error=" in r.headers["location"]
    assert slots(client)["A-05"]["visit"] is None

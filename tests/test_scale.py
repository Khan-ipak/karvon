"""Weighbridge integration (#11): parsing indicator output, TCP, the simulator, the UI."""

import socket
import threading
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from karvon import scale
from karvon.config import Settings
from karvon.main import create_app
from tests.conftest import ADMIN_PASSWORD, gate_in, login


@pytest.mark.parametrize(
    ("line", "kg", "stable"),
    [
        ("ST,GS,+0031240kg", 31240, True),
        ("US,GS,+0031180kg", 31180, False),
        ("OL,GS,+9999999kg", 9999999, False),
        ("S S     31240 kg", 31240, True),  # Mettler Toledo SICS
        ("S D     31200 kg", 31200, False),
        ("=0031240", 31240, None),  # no status: decided by repeated readings
        ("  31.24 t", 31240, None),
        ("ST,NT,+0012345,5kg", 12346, True),  # decimal comma, rounded to 1 kg
    ],
)
def test_parse_line(line, kg, stable):
    reading = scale.parse_line(line)
    assert reading.weight_kg == Decimal(kg)
    assert reading.stable is stable


def test_parse_line_without_weight():
    assert scale.parse_line("ERR") is None
    assert scale.parse_line("") is None


def test_custom_pattern():
    reading = scale.parse_line("W1:000.000 W2:0031240", r"W2:(?P<weight>\d+)")
    assert reading.weight_kg == 31240


def test_first_stable_waits_for_status():
    lines = iter(["US,GS,+0031000kg", "US,GS,+0031200kg", "ST,GS,+0031240kg", "never read"])
    assert scale.first_stable(lines).weight_kg == 31240


def test_first_stable_without_status_needs_repeated_readings():
    lines = iter(["=0031000", "=0031240", "=0031240", "=0031240"])
    reading = scale.first_stable(lines)
    assert reading.weight_kg == 31240 and reading.stable is True


def test_moving_truck_is_an_error():
    with pytest.raises(scale.ScaleError) as e:
        scale.first_stable(iter(["US,GS,+0031000kg", "=0031240", "=0031260"]))
    assert e.value.code == "scale_unstable"
    with pytest.raises(scale.ScaleError) as e:
        scale.first_stable(iter(["garbage"]))
    assert e.value.code == "scale_no_data"


def test_simulator_gross_and_tare():
    gross = scale.read("simulator")
    tare = scale.read("sim://", hint="tare")
    assert 30000 <= gross.weight_kg < 44000 and gross.stable
    assert 13500 <= tare.weight_kg < 16500


def _fake_indicator(lines: list[bytes], expect: bytes = b"") -> int:
    """A TCP indicator on localhost that sends `lines` (after `expect`, if given)."""
    server = socket.create_server(("127.0.0.1", 0))

    def serve():
        with server:
            conn, _ = server.accept()
            with conn:
                if expect:
                    assert conn.recv(len(expect)) == expect
                for line in lines:
                    conn.sendall(line)

    threading.Thread(target=serve, daemon=True).start()
    return server.getsockname()[1]


def test_tcp_scale():
    # A stream cut at odd places, as serial-to-Ethernet converters do.
    port = _fake_indicator([b"US,GS,+00312", b"00kg\r\nST,GS,+0031", b"240kg\r\n"])
    reading = scale.read(f"tcp://127.0.0.1:{port}", timeout=2)
    assert reading.weight_kg == 31240
    assert reading.raw == "ST,GS,+0031240kg"


def test_tcp_scale_on_request():
    port = _fake_indicator([b"S S     18400 kg\r\n"], expect=b"SI\r\n")
    reading = scale.read(f"tcp://127.0.0.1:{port}?send=SI%0D%0A", timeout=2)
    assert reading.weight_kg == 18400


def test_tcp_scale_offline():
    with socket.create_server(("127.0.0.1", 0)) as s:
        port = s.getsockname()[1]
    with pytest.raises(scale.ScaleError) as e:
        scale.read(f"tcp://127.0.0.1:{port}", timeout=1)
    assert e.value.code == "scale_offline"


def test_bad_url():
    for url in ("ftp://x", "tcp://nohost"):
        with pytest.raises(scale.ScaleError) as e:
            scale.read(url)
        assert e.value.code == "scale_config"


def test_cli(capsys):
    scale.main(["simulator"])
    assert "kg" in capsys.readouterr().out


# --- API and web UI ----------------------------------------------------------


@pytest.fixture
def scale_client(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'scale.db'}",
        seed_demo=False,
        admin_password=ADMIN_PASSWORD,
        scale_url="simulator",
    )
    with TestClient(create_app(settings)) as c:
        yield login(c, "admin", ADMIN_PASSWORD)


def test_api_reads_the_scale(scale_client):
    r = scale_client.get("/api/v1/scale")
    assert r.status_code == 200, r.text
    assert 30000 <= Decimal(r.json()["weight_kg"]) < 44000
    r = scale_client.get("/api/v1/scale?hint=tare")
    assert Decimal(r.json()["weight_kg"]) < 16500


def test_api_without_a_scale(client):
    r = client.get("/api/v1/scale")
    assert r.status_code == 409
    assert r.json()["code"] == "scale_not_configured"


def test_only_weighing_roles_read_the_scale(scale_client):
    from tests.test_auth import _add_user, _as

    pw = _add_user(scale_client, "broker", "customs")
    _as(scale_client, "broker", pw)
    assert scale_client.get("/api/v1/scale").status_code == 403


def test_no_scale_buttons_without_a_scale(client):
    vid = gate_in(client)["id"]
    assert "/scale/gross_kg" not in client.get(f"/visits/{vid}").text


def test_read_from_scale_in_the_web_ui(scale_client):
    vid = gate_in(scale_client)["id"]
    page = scale_client.get(f"/visits/{vid}").text
    assert f'formaction="/visits/{vid}/scale/gross_kg"' in page

    # The tare typed by hand is kept; the gross comes from the scale.
    r = scale_client.post(
        f"/visits/{vid}/scale/gross_kg",
        data={"gross_kg": "", "tare_kg": "15000"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    location = r.headers["location"]
    assert "tare_kg=15000" in location and location.endswith("#weigh")
    page = scale_client.get(location.split("#")[0]).text
    assert 'value="15000"' in page
    gross = location.split("gross_kg=")[1].split("&")[0]
    assert f'value="{gross}"' in page

    # Nothing is recorded until the operator presses "Record weight".
    assert scale_client.get(f"/api/v1/visits/{vid}").json()["weigh_tickets"] == []
    scale_client.post(f"/visits/{vid}/action/weigh", data={"gross_kg": gross, "tare_kg": "15000"})
    tickets = scale_client.get(f"/api/v1/visits/{vid}").json()["weigh_tickets"]
    assert Decimal(tickets[0]["net_kg"]) == Decimal(gross) - 15000


def test_scale_error_in_the_web_ui(tmp_path):
    with socket.create_server(("127.0.0.1", 0)) as s:
        port = s.getsockname()[1]
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'off.db'}",
        seed_demo=False,
        admin_password=ADMIN_PASSWORD,
        scale_url=f"tcp://127.0.0.1:{port}",
        scale_timeout=1,
    )
    with TestClient(create_app(settings)) as c:
        login(c, "admin", ADMIN_PASSWORD)
        c.cookies.set("lang", "ru")
        vid = gate_in(c)["id"]
        r = c.post(f"/visits/{vid}/scale/tare_kg", follow_redirects=True)
        assert "Нет связи с весами" in r.text

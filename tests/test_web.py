"""Smoke tests for the HTML UI, in every bundled language."""

import json
from pathlib import Path

import pytest

from karvon.i18n import languages

PAGES = ["/", "/gate", "/yard", "/stock", "/visits/1", "/visits/2", "/visits/3"]


@pytest.mark.parametrize("lang", sorted(languages()))
def test_pages_render(demo_client, lang):
    demo_client.cookies.set("lang", lang)
    for page in PAGES:
        r = demo_client.get(page)
        assert r.status_code == 200, (page, r.text[:500])
        assert "Karvon" in r.text


def test_translations_have_same_keys():
    d = Path(__file__).parents[1] / "karvon" / "i18n"
    en = set(json.loads((d / "en.json").read_text()))
    for path in d.glob("*.json"):
        assert set(json.loads(path.read_text())) == en, path.name


def test_gate_form_flow(client):
    r = client.post(
        "/gate",
        data={"plate_number": "01Z999ZZ", "driver_name": "Ali", "driver_passport": "AB1"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    url = r.headers["location"]
    page = client.get(url)
    assert "01Z999ZZ" in page.text

    r = client.post(f"{url}/action/park", follow_redirects=False)
    assert r.headers["location"] == url
    assert "A-01" in client.get(url).text

    # errors come back as a message, not a 500
    r = client.post(f"{url}/action/weigh", data={"gross_kg": "abc", "tare_kg": "1"})
    assert r.status_code == 200 and "Not a number" in r.text


def test_language_from_query(demo_client):
    assert "Обзор терминала" in demo_client.get("/?lang=ru").text
    assert "Terminal overview" in demo_client.get("/?lang=xx").text


def test_charges_are_translated(demo_client):
    # demo visit 2 is unloading 18 t into the warehouse; completing it adds a charge
    demo_client.post("/api/v1/operations/1/complete")
    page = demo_client.get("/visits/2?lang=ru").text
    assert "Выгрузка на склад" in page
    assert "warehouse_unload, op #1" not in page


def test_custom_tariff_falls_back_to_description(demo_client):
    from decimal import Decimal

    from karvon.models import Charge, Tariff, TariffUnit

    with demo_client.app.state.sessionmaker() as s, s.begin():
        s.add(Tariff(code="reefer_power", name="Reefer power", unit=TariffUnit.day, price=1))
        s.add(
            Charge(
                visit_id=2,
                tariff_code="reefer_power",
                description="Reefer power supply",
                quantity=Decimal(1),
                unit_price=Decimal(1),
                amount=Decimal(1),
            )
        )
    page = demo_client.get("/visits/2?lang=ru").text
    assert "Reefer power supply" in page
    assert "tariff.reefer_power" not in page

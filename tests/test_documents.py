"""CMR and invoice PDFs, and the shipping details they are filled from (#12)."""

import io

import pytest
from fpdf import FPDF  # noqa: F401  (the dependency must be installed)

from tests.conftest import gate_in

pypdf = pytest.importorskip("pypdf")


def _text(pdf_bytes: bytes) -> str:
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() for page in reader.pages)


def _shipping_visit(client):
    visit = gate_in(
        client,
        plate="KBL-4521",
        trailer_number="KBL-T77",
        tir_carnet_number="XK-55102231",
        cmr_number="AF-0431726",
        cargo_description="Dried fruit, 18 t",
        sender="Mazar Dry Fruits Co., Mazar-i-Sharif",
        consignee="Termez Fruit Trade LLC, Termez",
        loading_place="Mazar-i-Sharif, AF",
        delivery_place="Termez, UZ",
    )
    client.post(
        f"/api/v1/visits/{visit['id']}/weigh", json={"gross_kg": "38000", "tare_kg": "20000"}
    )
    return visit


def test_gate_in_stores_shipping_details(client):
    visit = _shipping_visit(client)
    assert visit["sender"] == "Mazar Dry Fruits Co., Mazar-i-Sharif"
    assert visit["delivery_place"] == "Termez, UZ"


def test_cmr_pdf(client):
    vid = _shipping_visit(client)["id"]
    r = client.get(f"/api/v1/visits/{vid}/cmr.pdf")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")
    text = _text(r.content)
    for expected in [
        "AF-0431726",
        "Mazar Dry Fruits Co.",
        "Termez Fruit Trade LLC",
        "Termez, UZ",
        "XK-55102231",
        "Dried fruit, 18 t",
        "18 000",  # net weight from the weigh ticket
        "KBL-4521",
        "KBL-T77",
        "МЕЖДУНАРОДНАЯ",  # Cyrillic needs the bundled font
    ]:
        assert expected in text, expected


def test_cmr_pdf_for_a_bare_visit(client):
    """A visit with no shipping details still gives a blank form to fill in by hand."""
    vid = gate_in(client)["id"]
    text = _text(client.get(f"/api/v1/visits/{vid}/cmr.pdf").content)
    assert "Sender" in text and "01A123BC" in text


def test_invoice_pdf(client):
    vid = gate_in(client)["id"]
    r = client.get(f"/api/v1/visits/{vid}/invoice.pdf")
    assert r.status_code == 409
    assert r.json()["code"] == "no_charges"

    client.post(f"/api/v1/visits/{vid}/park", json={})
    client.post(f"/api/v1/visits/{vid}/request-exit")
    text = _text(client.get(f"/api/v1/visits/{vid}/invoice.pdf?lang=ru").content)
    assert "СЧЁТ" in text and "К оплате" in text and "01A123BC" in text

    client.post(f"/api/v1/visits/{vid}/pay")
    text = _text(client.get(f"/api/v1/visits/{vid}/invoice.pdf").content)
    assert "INVOICE" in text and "PAID" in text


def test_update_documents_api_and_history(client):
    vid = gate_in(client)["id"]
    r = client.patch(
        f"/api/v1/visits/{vid}/documents",
        json={"consignee": "Tashkent Textile House", "cmr_number": "PK-1"},
    )
    assert r.status_code == 200
    assert r.json()["consignee"] == "Tashkent Textile House"
    # fields that were not sent are left alone; an empty string clears one
    r = client.patch(f"/api/v1/visits/{vid}/documents", json={"cmr_number": ""})
    assert r.json()["cmr_number"] is None
    assert r.json()["consignee"] == "Tashkent Textile House"
    actions = [e["action"] for e in client.get(f"/api/v1/visits/{vid}/history").json()]
    assert actions.count("documents") == 2


def test_documents_in_the_web_ui(client):
    vid = gate_in(client)["id"]
    r = client.post(
        f"/visits/{vid}/action/documents",
        data={"sender": "Lahore Textile Mills", "loading_place": "Lahore, PK"},
        follow_redirects=False,
    )
    assert "error" not in r.headers["location"]
    page = client.get(f"/visits/{vid}").text
    assert "Lahore Textile Mills" in page
    assert f"/visits/{vid}/cmr.pdf" in page
    assert "invoice.pdf" not in page  # no charges yet

    r = client.get(f"/visits/{vid}/cmr.pdf")
    assert r.headers["content-type"] == "application/pdf"
    assert f'filename="cmr-{vid}-01A123BC.pdf"' in r.headers["content-disposition"]


def test_only_gate_role_edits_documents(client):
    from tests.test_auth import _add_user, _as

    vid = gate_in(client)["id"]
    pw = _add_user(client, "acc", "accountant")
    _as(client, "acc", pw)
    r = client.patch(f"/api/v1/visits/{vid}/documents", json={"sender": "X"})
    assert r.status_code == 403
    assert client.get(f"/api/v1/visits/{vid}/cmr.pdf").status_code == 200  # anyone can print

"""Printable PDF documents for a visit: the CMR consignment note and the invoice.

Uses fpdf2 (pure Python) with the bundled DejaVu Sans font, which covers Latin,
Cyrillic and Uzbek letters. The CMR follows the layout of the standard form: the
numbered boxes 1-25, labelled in English and Russian as is usual on CMR forms used
in Central Asia. The invoice is in the user's interface language.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from fpdf import FPDF

from karvon.config import Settings
from karvon.models import Visit, as_utc, utcnow

FONTS = Path(__file__).parent / "fonts"
FONT = "DejaVu"

INK = (17, 24, 39)
MUTED = (100, 116, 139)
LINE = (148, 163, 184)
ACCENT = (37, 99, 235)
PAID = (22, 163, 74)

Translate = Callable[[str], str]


class _Doc(FPDF):
    def __init__(self) -> None:
        super().__init__(format="A4", unit="mm")
        self.add_font(FONT, "", FONTS / "DejaVuSans.ttf")
        self.add_font(FONT, "B", FONTS / "DejaVuSans-Bold.ttf")
        self.set_auto_page_break(False)
        self.set_margins(12, 12, 12)
        self.set_creator("Karvon")
        self.add_page()
        self.set_draw_color(*LINE)
        self.set_line_width(0.3)

    def write_at(
        self,
        x: float,
        y: float,
        w: float,
        text: str,
        size: float,
        *,
        bold: bool = False,
        color=INK,
        align: str = "L",
    ) -> None:
        self.set_font(FONT, "B" if bold else "", size)
        self.set_text_color(*color)
        self.set_xy(x, y)
        self.cell(w, size * 0.45, text, align=align)

    def text_box(self, x: float, y: float, w: float, h: float, text: str, size: float) -> None:
        """Wrapped text that is cut off rather than spilling out of its box."""
        self.set_font(FONT, "", size)
        self.set_text_color(*INK)
        line_h = size * 0.45
        lines = self.multi_cell(w, line_h, text, align="L", dry_run=True, output="LINES")
        fits = max(1, int(h // line_h))
        self.set_xy(x, y)
        self.multi_cell(w, line_h, "\n".join(lines[:fits]), align="L")

    def output_bytes(self) -> bytes:
        return bytes(self.output())


def _local(value: datetime, settings: Settings) -> datetime:
    return as_utc(value).astimezone(settings.tz)


def _lines(*parts: str | None) -> str:
    return "\n".join(p for p in parts if p)


def _cargo_weight(visit: Visit) -> Decimal | None:
    """Net weight from the latest weigh ticket, if the truck was weighed."""
    if not visit.weigh_tickets:
        return None
    return visit.weigh_tickets[-1].net_kg


# --- CMR ---------------------------------------------------------------------


def _cmr_box(
    doc: _Doc,
    x: float,
    y: float,
    w: float,
    h: float,
    number: str,
    label: str,
    value: str = "",
    size: float = 9,
) -> None:
    doc.rect(x, y, w, h)
    doc.write_at(x + 1.2, y + 1.2, 6, number, 7, bold=True, color=ACCENT)
    doc.set_font(FONT, "", 5.6)
    doc.set_text_color(*MUTED)
    doc.set_xy(x + 6, y + 1.1)
    doc.multi_cell(w - 7, 2.4, label, align="L")
    top = doc.get_y() + 1
    if value:
        doc.text_box(x + 2, top, w - 4, y + h - top - 1, value, size)


def cmr_pdf(visit: Visit, settings: Settings) -> bytes:
    """A filled CMR (international consignment note) for printing and signing."""
    doc = _Doc()
    doc.set_title(f"CMR {visit.cmr_number or visit.id}")
    left, top, width = 12.0, 12.0, 186.0
    half = width / 2
    x2 = left + half
    now = _local(utcnow(), settings)
    weight = _cargo_weight(visit)

    # Left column: boxes 1-5. Right column: the title, then boxes 16-18.
    y = top
    _cmr_box(
        doc,
        left,
        y,
        half,
        27,
        "1",
        "Sender (name, address, country) / Отправитель (наименование, адрес, страна)",
        visit.sender or "",
    )
    _cmr_box(
        doc,
        left,
        y + 27,
        half,
        27,
        "2",
        "Consignee (name, address, country) / Получатель (наименование, адрес, страна)",
        visit.consignee or "",
    )
    _cmr_box(
        doc,
        left,
        y + 54,
        half,
        15,
        "3",
        "Place of delivery of the goods / Место разгрузки груза",
        visit.delivery_place or "",
    )
    _cmr_box(
        doc,
        left,
        y + 69,
        half,
        15,
        "4",
        "Place and date of taking over the goods / Место и дата погрузки груза",
        visit.loading_place or "",
    )
    _cmr_box(
        doc,
        left,
        y + 84,
        half,
        15,
        "5",
        "Documents attached / Прилагаемые документы",
        f"TIR carnet / Книжка МДП: {visit.tir_carnet_number}" if visit.tir_carnet_number else "",
    )

    doc.rect(x2, y, half, 27)
    doc.write_at(x2 + 3, y + 3, 30, "CMR", 22, bold=True, color=ACCENT)
    doc.write_at(
        x2 + 30, y + 3.5, half - 33, f"№ {visit.cmr_number or '—'}", 12, bold=True, align="R"
    )
    doc.set_font(FONT, "B", 7)
    doc.set_text_color(*INK)
    doc.set_xy(x2 + 3, y + 14)
    doc.multi_cell(
        half - 6, 3, "INTERNATIONAL CONSIGNMENT NOTE\nМЕЖДУНАРОДНАЯ ТОВАРНО-ТРАНСПОРТНАЯ НАКЛАДНАЯ"
    )
    _cmr_box(
        doc,
        x2,
        y + 27,
        half,
        27,
        "16",
        "Carrier (name, address, country) / Перевозчик (наименование, адрес, страна)",
        _lines(
            visit.driver.full_name,
            f"Passport / Паспорт: {visit.driver.passport_number}",
            visit.driver.phone,
            visit.vehicle.country,
        ),
    )
    _cmr_box(doc, x2, y + 54, half, 20, "17", "Successive carriers / Последующие перевозчики")
    _cmr_box(
        doc,
        x2,
        y + 74,
        half,
        25,
        "18",
        "Carrier's reservations and observations / Оговорки и замечания перевозчика",
    )

    # Goods table: boxes 6-12.
    y = top + 99
    h = 48
    cols = [
        ("6", "Marks and Nos\nЗнаки и номера", 22),
        ("7", "Number of packages\nКол-во мест", 20),
        ("8", "Method of packing\nРод упаковки", 22),
        ("9", "Nature of the goods\nНаименование груза", 64),
        ("10", "Statistical No.\nСтат. номер", 20),
        ("11", "Gross weight, kg\nВес брутто, кг", 20),
        ("12", "Volume, m³\nОбъём, м³", 18),
    ]
    x = left
    for number, label, w in cols:
        value = ""
        if number == "9":
            value = visit.cargo_description or ""
        elif number == "11" and weight is not None:
            value = f"{weight:,.0f}".replace(",", " ")
        _cmr_box(doc, x, y, w, h, number, label, value, size=9 if number != "11" else 10)
        x += w

    y += h
    _cmr_box(
        doc,
        left,
        y,
        half,
        22,
        "13",
        "Sender's instructions (customs and other formalities) / "
        "Указания отправителя (таможенные и прочие формальности)",
    )
    _cmr_box(doc, x2, y, half, 22, "19", "Special agreements / Особые согласования")
    y += 22
    _cmr_box(doc, left, y, half, 14, "14", "Cash on delivery / Возврат наложенного платежа")
    _cmr_box(doc, x2, y, half, 14, "20", "To be paid by / Подлежит оплате")
    y += 14
    _cmr_box(
        doc,
        left,
        y,
        width,
        13,
        "21",
        "Established in / Составлена в",
        f"{settings.terminal_name}, {now:%d.%m.%Y}",
    )
    y += 13

    third = width / 3
    signatures = [
        ("22", "Signature and stamp of the sender\nПодпись и штамп отправителя"),
        ("23", "Signature and stamp of the carrier\nПодпись и штамп перевозчика"),
        (
            "24",
            "Goods received: signature and stamp of the consignee\n"
            "Груз получен: подпись и штамп получателя",
        ),
    ]
    for i, (number, label) in enumerate(signatures):
        _cmr_box(doc, left + i * third, y, third, 34, number, label)
    y += 34

    plates = visit.vehicle.plate_number
    if visit.vehicle.trailer_number:
        plates += f"  /  {visit.vehicle.trailer_number}"
    _cmr_box(
        doc,
        left,
        y,
        width,
        13,
        "25",
        "Registration number: tractor / trailer / Регистрационный номер: тягач / прицеп",
        plates,
        size=11,
    )
    y += 13

    doc.write_at(
        left,
        y + 3,
        width,
        f"Karvon · {settings.terminal_name} · visit #{visit.id} · {now:%Y-%m-%d %H:%M}",
        6.5,
        color=MUTED,
    )
    return doc.output_bytes()


# --- invoice -----------------------------------------------------------------


def _money(value: Decimal, currency: str) -> str:
    return f"{value:,.2f}".replace(",", " ") + f" {currency}"


def charge_name(charge, t: Translate) -> str:
    """Translated tariff name, or the stored description for custom tariffs."""
    name = t(f"tariff.{charge.tariff_code}")
    return charge.description if name.startswith("tariff.") else name


def invoice_pdf(visit: Visit, settings: Settings, t: Translate) -> bytes:
    doc = _Doc()
    number = f"{visit.id:06d}"
    doc.set_title(f"{t('doc.invoice')} {number}")
    left, width = 12.0, 186.0
    now = _local(utcnow(), settings)
    currency = settings.currency

    # Header: terminal on the left, invoice number and date on the right.
    doc.write_at(left, 14, 110, settings.terminal_name, 16, bold=True)
    if settings.terminal_address:
        doc.set_font(FONT, "", 9)
        doc.set_text_color(*MUTED)
        doc.set_xy(left, 22)
        doc.multi_cell(110, 4.2, settings.terminal_address, align="L")
    doc.write_at(
        left + 110,
        14,
        width - 110,
        t("doc.invoice").upper(),
        16,
        bold=True,
        color=ACCENT,
        align="R",
    )
    doc.write_at(left + 110, 22, width - 110, f"№ {number}", 11, bold=True, align="R")
    doc.write_at(
        left + 110, 28, width - 110, f"{t('doc.date')}: {now:%d.%m.%Y}", 9, color=MUTED, align="R"
    )

    doc.set_draw_color(*ACCENT)
    doc.set_line_width(0.6)
    doc.line(left, 38, left + width, 38)
    doc.set_draw_color(*LINE)
    doc.set_line_width(0.3)

    # Who and what: vehicle, driver, visit dates, cargo.
    vehicle = visit.vehicle.plate_number
    if visit.vehicle.trailer_number:
        vehicle += f" / {visit.vehicle.trailer_number}"
    if visit.vehicle.country:
        vehicle += f" ({visit.vehicle.country})"
    stay = _local(visit.arrived_at, settings).strftime("%d.%m.%Y %H:%M")
    if visit.exited_at:
        stay += " – " + _local(visit.exited_at, settings).strftime("%d.%m.%Y %H:%M")
    facts = [
        (t("doc.vehicle"), vehicle),
        (t("doc.driver"), f"{visit.driver.full_name} ({visit.driver.passport_number})"),
        (t("doc.visit"), f"#{visit.id} · {stay}"),
    ]
    if visit.cmr_number:
        facts.append((t("visit.cmr"), visit.cmr_number))
    if visit.cargo_description:
        facts.append((t("visit.cargo"), visit.cargo_description))
    y = 43
    for label, value in facts:
        doc.write_at(left, y, 40, label, 9, color=MUTED)
        doc.set_xy(left + 40, y)
        doc.text_box(left + 40, y, width - 40, 9, value, 9.5)
        y = max(y + 5.5, doc.get_y() + 1.5)

    # Charges table.
    y += 4
    cols = [
        ("#", 10, "L"),
        (t("charges.desc"), 92, "L"),
        (t("charges.qty"), 22, "R"),
        (t("charges.price"), 30, "R"),
        (t("charges.amount"), 32, "R"),
    ]
    doc.set_fill_color(241, 245, 249)
    doc.rect(left, y, width, 8, style="F")
    x = left
    for title, w, align in cols:
        doc.write_at(x + 1.5, y + 2.2, w - 3, title, 8.5, bold=True, color=MUTED, align=align)
        x += w
    y += 8
    for i, charge in enumerate(visit.charges, start=1):
        cells = [
            str(i),
            charge_name(charge, t),
            f"{charge.quantity.normalize():f}",
            _money(charge.unit_price, ""),
            _money(charge.amount, currency),
        ]
        x = left
        for (_, w, align), value in zip(cols, cells, strict=True):
            doc.write_at(x + 1.5, y + 2.4, w - 3, value.strip(), 9.5, align=align)
            x += w
        y += 8
        doc.line(left, y, left + width, y)

    # Totals.
    total = sum((c.amount for c in visit.charges), Decimal("0.00"))
    paid = sum((c.amount for c in visit.charges if c.paid), Decimal("0.00"))
    y += 4
    rows = [
        (t("doc.total"), total, True),
        (t("doc.paid"), paid, False),
        (t("doc.due"), total - paid, True),
    ]
    for label, amount, bold in rows:
        doc.write_at(left + 100, y, 52, label, 10, bold=bold, align="R")
        doc.write_at(left + 154, y, 32, _money(amount, currency), 10, bold=bold, align="R")
        y += 6.5

    if visit.charges and paid == total:
        doc.set_draw_color(*PAID)
        doc.set_line_width(0.8)
        doc.rect(left, y - 19, 42, 13)
        doc.write_at(
            left, y - 15.5, 42, t("doc.paid_stamp").upper(), 14, bold=True, color=PAID, align="C"
        )

    doc.write_at(
        left,
        282,
        width,
        f"{t('doc.generated')} Karvon · {now:%Y-%m-%d %H:%M} ({settings.timezone})",
        7,
        color=MUTED,
    )
    return doc.output_bytes()

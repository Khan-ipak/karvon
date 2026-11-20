"""Server-rendered web UI. Plain HTML forms, no frontend build step."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from karvon import services
from karvon.db import get_session
from karvon.i18n import languages, translator
from karvon.models import OperationType, StockLot, Visit, VisitStatus, YardZone

DB = Annotated[Session, Depends(get_session)]

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
router = APIRouter(include_in_schema=False)


def _lang(request: Request) -> str:
    lang = (
        request.query_params.get("lang")
        or request.cookies.get("lang")
        or request.app.state.settings.default_language
    )
    return lang if lang in languages() else "en"


def render(request: Request, name: str, **context) -> HTMLResponse:
    settings = request.app.state.settings
    return templates.TemplateResponse(
        request,
        name,
        {
            "t": translator(_lang(request)),
            "lang": _lang(request),
            "languages": languages(),
            "terminal_name": settings.terminal_name,
            "currency": settings.currency,
            "error": request.query_params.get("error"),
            **context,
        },
    )


def back_to(url: str, error: str | None = None) -> RedirectResponse:
    if error:
        url = f"{url}?error={quote(error)}"
    return RedirectResponse(url, status_code=303)


def _decimal(value: str) -> Decimal:
    try:
        return Decimal(value.replace(",", ".").strip())
    except InvalidOperation as exc:
        raise services.DomainError("invalid_number", f"Not a number: {value!r}") from exc


def _opt(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


# --- pages -------------------------------------------------------------------


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: DB, q: str = ""):
    return render(
        request,
        "dashboard.html",
        counts=services.dashboard_counts(db),
        visits=services.list_visits(db, on_site_only=True, plate=q),
        q=q.strip(),
    )


@router.get("/gate", response_class=HTMLResponse)
def gate_form(request: Request):
    return render(request, "gate.html")


@router.post("/gate")
def gate_submit(
    db: DB,
    plate_number: Annotated[str, Form()],
    driver_name: Annotated[str, Form()],
    driver_passport: Annotated[str, Form()],
    trailer_number: Annotated[str | None, Form()] = None,
    country: Annotated[str | None, Form()] = None,
    driver_phone: Annotated[str | None, Form()] = None,
    tir_carnet_number: Annotated[str | None, Form()] = None,
    cmr_number: Annotated[str | None, Form()] = None,
    cargo_description: Annotated[str | None, Form()] = None,
):
    data = services.GateInData(
        plate_number=plate_number,
        driver_name=driver_name,
        driver_passport=driver_passport,
        trailer_number=_opt(trailer_number),
        country=_opt(country),
        driver_phone=_opt(driver_phone),
        tir_carnet_number=_opt(tir_carnet_number),
        cmr_number=_opt(cmr_number),
        cargo_description=_opt(cargo_description),
    )
    try:
        visit = services.gate_in(db, data)
    except services.DomainError as e:
        db.rollback()
        return back_to("/gate", e.message)
    return back_to(f"/visits/{visit.id}")


@router.get("/visits/{visit_id}", response_class=HTMLResponse)
def visit_page(request: Request, visit_id: int, db: DB):
    visit = services.get(db, Visit, visit_id)
    others = [v for v in services.list_visits(db, on_site_only=True) if v.id != visit.id]
    return render(
        request,
        "visit.html",
        visit=visit,
        free_slots=services.free_slots(db),
        other_visits=[v for v in others if v.status != VisitStatus.awaiting_payment],
        lots=list(db.scalars(select(StockLot).where(StockLot.quantity_kg > 0))),
        operation_types=list(OperationType),
        parking_days=services.parking_days(visit),
    )


@router.get("/yard", response_class=HTMLResponse)
def yard_page(request: Request, db: DB):
    zones = list(db.scalars(select(YardZone).order_by(YardZone.code)))
    return render(request, "yard.html", zones=zones)


@router.get("/stock", response_class=HTMLResponse)
def stock_page(request: Request, db: DB):
    lots = list(db.scalars(select(StockLot).where(StockLot.quantity_kg > 0)))
    return render(request, "stock.html", lots=lots)


@router.get("/lang/{code}")
def set_language(request: Request, code: str):
    target = request.headers.get("referer") or "/"
    response = RedirectResponse(target, status_code=303)
    if code in languages():
        response.set_cookie("lang", code, max_age=365 * 86400, samesite="lax")
    return response


# --- visit actions -----------------------------------------------------------


@router.post("/visits/{visit_id}/action/{action}")
def visit_action(
    visit_id: int,
    action: str,
    db: DB,
    slot_id: Annotated[str | None, Form()] = None,
    gross_kg: Annotated[str | None, Form()] = None,
    tare_kg: Annotated[str | None, Form()] = None,
    type: Annotated[str | None, Form()] = None,
    weight_kg: Annotated[str | None, Form()] = None,
    cargo_owner: Annotated[str | None, Form()] = None,
    target_visit_id: Annotated[str | None, Form()] = None,
    stock_lot_id: Annotated[str | None, Form()] = None,
    notes: Annotated[str | None, Form()] = None,
):
    url = f"/visits/{visit_id}"
    try:
        match action:
            case "park":
                services.park(db, visit_id, int(slot_id) if _opt(slot_id) else None)
            case "weigh":
                services.weigh(db, visit_id, _decimal(gross_kg or ""), _decimal(tare_kg or "0"))
            case "operation":
                services.plan_operation(
                    db,
                    visit_id,
                    services.OperationData(
                        type=OperationType(type),
                        weight_kg=_decimal(weight_kg or ""),
                        cargo_owner=_opt(cargo_owner),
                        notes=_opt(notes),
                        target_visit_id=int(target_visit_id) if _opt(target_visit_id) else None,
                        stock_lot_id=int(stock_lot_id) if _opt(stock_lot_id) else None,
                    ),
                )
            case "request-exit":
                services.request_exit(db, visit_id)
            case "pay":
                services.pay_visit(db, visit_id)
            case "gate-out":
                services.gate_out(db, visit_id)
            case _:
                return back_to(url, f"Unknown action {action}")
    except (services.DomainError, ValueError) as e:
        db.rollback()
        return back_to(url, getattr(e, "message", str(e)))
    return back_to(url)


@router.post("/operations/{operation_id}/action/{action}")
def operation_action(operation_id: int, action: str, db: DB):
    from karvon.models import Operation

    op = services.get(db, Operation, operation_id)
    url = f"/visits/{op.visit_id}"
    handlers = {
        "start": services.start_operation,
        "complete": services.complete_operation,
        "cancel": services.cancel_operation,
        "customs": services.set_customs_cleared,
    }
    handler = handlers.get(action)
    if handler is None:
        return back_to(url, f"Unknown action {action}")
    try:
        handler(db, operation_id)
    except services.DomainError as e:
        db.rollback()
        return back_to(url, e.message)
    return back_to(url)

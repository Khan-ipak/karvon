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

from karvon import auth, services
from karvon.db import get_session
from karvon.i18n import languages, translator
from karvon.models import (
    Operation,
    OperationType,
    Role,
    StockLot,
    User,
    Visit,
    VisitStatus,
    YardZone,
    utcnow,
)

DB = Annotated[Session, Depends(get_session)]

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def web_user(request: Request, db: DB) -> User:
    user = auth.request_user(request, db)
    if user is None:
        target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        raise auth.LoginRequired(target)
    return user


CurrentUser = Annotated[User, Depends(web_user)]

# `router` holds public pages (login, language); `pages` needs a logged-in user.
router = APIRouter(include_in_schema=False)
pages = APIRouter(include_in_schema=False, dependencies=[Depends(web_user)])


def _lang(request: Request) -> str:
    lang = (
        request.query_params.get("lang")
        or request.cookies.get("lang")
        or request.app.state.settings.default_language
    )
    return lang if lang in languages() else "en"


def render(request: Request, name: str, **context) -> HTMLResponse:
    settings = request.app.state.settings
    user = getattr(request.state, "user", None)
    return templates.TemplateResponse(
        request,
        name,
        {
            "user": user,
            "can": lambda permission: auth.can(user, permission),
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


def _check(request: Request, user: User, permission: str) -> None:
    """Like auth.check, but with a message in the user's language."""
    if not auth.can(user, permission):
        raise services.DomainError("forbidden", translator(_lang(request))("auth.forbidden"))


def _decimal(value: str) -> Decimal:
    try:
        return Decimal(value.replace(",", ".").strip())
    except InvalidOperation as exc:
        raise services.DomainError("invalid_number", f"Not a number: {value!r}") from exc


def _opt(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


# --- pages -------------------------------------------------------------------


@pages.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: DB, q: str = ""):
    return render(
        request,
        "dashboard.html",
        counts=services.dashboard_counts(db),
        visits=services.list_visits(db, on_site_only=True, plate=q),
        q=q.strip(),
    )


@pages.get("/gate", response_class=HTMLResponse)
def gate_form(request: Request):
    return render(request, "gate.html")


@pages.post("/gate")
def gate_submit(
    request: Request,
    user: CurrentUser,
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
        _check(request, user, "gate")
        visit = services.gate_in(db, data)
    except services.DomainError as e:
        db.rollback()
        return back_to("/gate", e.message)
    return back_to(f"/visits/{visit.id}")


@pages.get("/visits/{visit_id}", response_class=HTMLResponse)
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


@pages.get("/visits/{visit_id}/pass", response_class=HTMLResponse)
def exit_pass(request: Request, visit_id: int, db: DB):
    """Printable slip the gate guard keeps when the truck leaves."""
    visit = services.get(db, Visit, visit_id)
    if visit.status not in (VisitStatus.cleared, VisitStatus.exited):
        t = translator(_lang(request))
        return back_to(f"/visits/{visit_id}", t("pass.not_cleared"))
    return render(
        request,
        "pass.html",
        visit=visit,
        total_paid=sum((c.amount for c in visit.charges if c.paid), Decimal("0.00")),
        issued_at=utcnow(),
    )


@pages.get("/yard", response_class=HTMLResponse)
def yard_page(request: Request, db: DB):
    zones = list(db.scalars(select(YardZone).order_by(YardZone.code)))
    return render(request, "yard.html", zones=zones)


@pages.get("/stock", response_class=HTMLResponse)
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

VISIT_ACTIONS = {
    "park": "park",
    "weigh": "weigh",
    "operation": "plan_operation",
    "request-exit": "gate",
    "pay": "pay",
    "gate-out": "gate",
}

OPERATION_ACTIONS = {
    "start": (services.start_operation, "run_operation"),
    "complete": (services.complete_operation, "run_operation"),
    "cancel": (services.cancel_operation, "cancel_operation"),
    "customs": (services.set_customs_cleared, "customs"),
}


@pages.post("/visits/{visit_id}/action/{action}")
def visit_action(
    request: Request,
    user: CurrentUser,
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
    permission = VISIT_ACTIONS.get(action)
    if permission is None:
        return back_to(url, f"Unknown action {action}")
    try:
        _check(request, user, permission)
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
    except (services.DomainError, ValueError) as e:
        db.rollback()
        return back_to(url, getattr(e, "message", str(e)))
    return back_to(url)


@pages.post("/operations/{operation_id}/action/{action}")
def operation_action(request: Request, user: CurrentUser, operation_id: int, action: str, db: DB):
    op = services.get(db, Operation, operation_id)
    url = f"/visits/{op.visit_id}"
    if action not in OPERATION_ACTIONS:
        return back_to(url, f"Unknown action {action}")
    handler, permission = OPERATION_ACTIONS[action]
    try:
        _check(request, user, permission)
        handler(db, operation_id)
    except services.DomainError as e:
        db.rollback()
        return back_to(url, e.message)
    return back_to(url)


# --- login -------------------------------------------------------------------


def _demo(request: Request) -> bool:
    return request.app.state.settings.seed_demo


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/"):
    return render(request, "login.html", next=auth.safe_next(next), demo=_demo(request))


@router.post("/login")
def login_submit(
    request: Request,
    db: DB,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    next: Annotated[str, Form()] = "/",
):
    user = auth.authenticate(db, username, password)
    if user is None:
        t = translator(_lang(request))
        return render(
            request,
            "login.html",
            next=auth.safe_next(next),
            username=username,
            demo=_demo(request),
            error=t("auth.bad_login"),
        )
    token = auth.start_session(db, user)
    response = RedirectResponse(auth.safe_next(next), status_code=303)
    response.set_cookie(
        auth.SESSION_COOKIE,
        token,
        max_age=int(auth.SESSION_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return response


@router.post("/logout")
def logout(request: Request, db: DB):
    if token := request.cookies.get(auth.SESSION_COOKIE):
        auth.end_session(db, token)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(auth.SESSION_COOKIE)
    return response


# --- users (admin) -----------------------------------------------------------


@pages.get("/users", response_class=HTMLResponse)
def users_page(request: Request, user: CurrentUser, db: DB):
    if not auth.can(user, "manage_users"):
        return back_to("/", translator(_lang(request))("auth.forbidden"))
    users = list(db.scalars(select(User).order_by(User.username)))
    return render(request, "users.html", users=users, roles=list(Role))


@pages.post("/users")
def users_create(
    request: Request,
    user: CurrentUser,
    db: DB,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    role: Annotated[str, Form()],
    full_name: Annotated[str | None, Form()] = None,
):
    try:
        _check(request, user, "manage_users")
        auth.create_user(db, username, password, Role(role), _opt(full_name))
    except (services.DomainError, ValueError) as e:
        db.rollback()
        return back_to("/users", getattr(e, "message", str(e)))
    return back_to("/users")


@pages.post("/users/{user_id}/{action}")
def users_action(
    request: Request,
    user: CurrentUser,
    user_id: int,
    action: str,
    db: DB,
    password: Annotated[str | None, Form()] = None,
):
    try:
        _check(request, user, "manage_users")
        target = services.get(db, User, user_id)
        match action:
            case "deactivate":
                if target.id == user.id:
                    raise services.DomainError("self", "You cannot deactivate yourself")
                auth.set_active(db, target, False)
            case "activate":
                auth.set_active(db, target, True)
            case "password":
                auth.set_password(db, target, password or "")
            case _:
                return back_to("/users", f"Unknown action {action}")
    except services.DomainError as e:
        db.rollback()
        return back_to("/users", e.message)
    return back_to("/users")


router.include_router(pages)

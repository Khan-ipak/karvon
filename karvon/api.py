"""REST API. Thin layer over `karvon.services`."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from karvon import auth, services
from karvon.db import get_session
from karvon.models import AuditEvent, Operation, StockLot, Tariff, User, Visit, YardZone
from karvon.schemas import (
    AuditEventOut,
    CustomsIn,
    ErrorOut,
    GateIn,
    OperationIn,
    OperationOut,
    ParkIn,
    StockLotOut,
    TariffOut,
    UserOut,
    VisitDetailOut,
    VisitOut,
    WeighIn,
    WeighTicketOut,
    ZoneOut,
)

DB = Annotated[Session, Depends(get_session)]


def api_user(request: Request, db: DB) -> User:
    """Every API call needs a login: the session cookie or HTTP Basic auth."""
    user = auth.request_user(request, db)
    if user is None:
        raise auth.NotAuthenticated()
    return user


def allow(permission: str):
    def dependency(user: Annotated[User, Depends(api_user)]) -> User:
        auth.check(user, permission)
        return user

    return Depends(dependency)


router = APIRouter(
    prefix="/api/v1",
    dependencies=[Depends(api_user)],
    responses={
        401: {"model": ErrorOut},
        403: {"model": ErrorOut},
        404: {"model": ErrorOut},
        409: {"model": ErrorOut},
    },
)


@router.get("/me", response_model=UserOut, tags=["users"])
def me(user: Annotated[User, Depends(api_user)]) -> User:
    return user


# --- visits / gate -----------------------------------------------------------


@router.get("/visits.csv", tags=["gate"])
def export_visits(
    db: DB,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query(alias="to")] = None,
) -> Response:
    """Download all visits as CSV, optionally filtered by arrival date."""
    text = services.export_visits_csv(db, arrived_from=from_, arrived_to=to)
    return Response(
        content=text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="visits.csv"'},
    )


@router.get("/visits", response_model=list[VisitOut], tags=["gate"])
def list_visits(db: DB, on_site: bool = False, plate: str | None = None) -> list[Visit]:
    return services.list_visits(db, on_site_only=on_site, plate=plate)


@router.post(
    "/visits",
    response_model=VisitDetailOut,
    status_code=201,
    tags=["gate"],
    dependencies=[allow("gate")],
)
def gate_in(body: GateIn, db: DB) -> Visit:
    return services.gate_in(db, services.GateInData(**body.model_dump()))


@router.get("/visits/{visit_id}", response_model=VisitDetailOut, tags=["gate"])
def get_visit(visit_id: int, db: DB) -> Visit:
    return services.get(db, Visit, visit_id)


@router.get("/visits/{visit_id}/history", response_model=list[AuditEventOut], tags=["gate"])
def visit_history(visit_id: int, db: DB) -> list[AuditEvent]:
    """Who did what with this visit, oldest first."""
    return services.get(db, Visit, visit_id).audit_events


@router.post(
    "/visits/{visit_id}/park",
    response_model=VisitDetailOut,
    tags=["yard"],
    dependencies=[allow("park")],
)
def park(visit_id: int, body: ParkIn, db: DB) -> Visit:
    return services.park(db, visit_id, body.slot_id)


@router.post(
    "/visits/{visit_id}/request-exit",
    response_model=VisitDetailOut,
    tags=["gate"],
    dependencies=[allow("gate")],
)
def request_exit(visit_id: int, db: DB) -> Visit:
    return services.request_exit(db, visit_id)


@router.post(
    "/visits/{visit_id}/pay",
    response_model=VisitDetailOut,
    tags=["billing"],
    dependencies=[allow("pay")],
)
def pay(visit_id: int, db: DB) -> Visit:
    return services.pay_visit(db, visit_id)


@router.post(
    "/visits/{visit_id}/gate-out",
    response_model=VisitDetailOut,
    tags=["gate"],
    dependencies=[allow("gate")],
)
def gate_out(visit_id: int, db: DB) -> Visit:
    return services.gate_out(db, visit_id)


# --- operations --------------------------------------------------------------


@router.post(
    "/visits/{visit_id}/operations",
    response_model=OperationOut,
    status_code=201,
    tags=["operations"],
    dependencies=[allow("plan_operation")],
)
def plan_operation(visit_id: int, body: OperationIn, db: DB) -> Operation:
    return services.plan_operation(db, visit_id, services.OperationData(**body.model_dump()))


@router.post(
    "/operations/{operation_id}/start",
    response_model=OperationOut,
    tags=["operations"],
    dependencies=[allow("run_operation")],
)
def start_operation(operation_id: int, db: DB) -> Operation:
    return services.start_operation(db, operation_id)


@router.post(
    "/operations/{operation_id}/complete",
    response_model=OperationOut,
    tags=["operations"],
    dependencies=[allow("run_operation")],
)
def complete_operation(operation_id: int, db: DB) -> Operation:
    return services.complete_operation(db, operation_id)


@router.post(
    "/operations/{operation_id}/cancel",
    response_model=OperationOut,
    tags=["operations"],
    dependencies=[allow("cancel_operation")],
)
def cancel_operation(operation_id: int, db: DB) -> Operation:
    return services.cancel_operation(db, operation_id)


@router.post(
    "/operations/{operation_id}/customs",
    response_model=OperationOut,
    tags=["customs"],
    dependencies=[allow("customs")],
)
def customs(operation_id: int, body: CustomsIn, db: DB) -> Operation:
    return services.set_customs_cleared(db, operation_id, body.cleared)


# --- weighbridge -------------------------------------------------------------


@router.post(
    "/visits/{visit_id}/weigh",
    response_model=WeighTicketOut,
    status_code=201,
    tags=["weighbridge"],
    dependencies=[allow("weigh")],
)
def weigh(visit_id: int, body: WeighIn, db: DB):
    return services.weigh(db, visit_id, body.gross_kg, body.tare_kg)


# --- reference data ----------------------------------------------------------


@router.get("/yard", response_model=list[ZoneOut], tags=["yard"])
def yard(db: DB) -> list[YardZone]:
    return list(db.scalars(select(YardZone).order_by(YardZone.code)))


@router.get("/stock", response_model=list[StockLotOut], tags=["warehouse"])
def stock(db: DB) -> list[StockLot]:
    return list(db.scalars(select(StockLot).where(StockLot.quantity_kg > 0).order_by(StockLot.id)))


@router.get("/tariffs", response_model=list[TariffOut], tags=["billing"])
def tariffs(db: DB) -> list[Tariff]:
    return list(db.scalars(select(Tariff).order_by(Tariff.code)))

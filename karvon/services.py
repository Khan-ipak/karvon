"""Business rules of the terminal.

Both the REST API and the web UI call these functions. Nothing here knows about HTTP.
Every function works inside the caller's session and only flushes; the caller commits.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from karvon.models import (
    Charge,
    Driver,
    Operation,
    OperationStatus,
    OperationType,
    StockLot,
    Tariff,
    Vehicle,
    Visit,
    VisitStatus,
    WeighTicket,
    YardSlot,
    as_utc,
    utcnow,
)

PARKING_TARIFF = "parking_day"
WEIGHBRIDGE_TARIFF = "weighbridge"

ON_SITE = (VisitStatus.registered, VisitStatus.parked, VisitStatus.in_operation)
OPEN_OPERATION = (OperationStatus.planned, OperationStatus.in_progress)


class DomainError(Exception):
    """A business rule was violated. `code` is stable and safe to show to API clients."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class NotFound(DomainError):
    def __init__(self, what: str, ident: object) -> None:
        super().__init__("not_found", f"{what} {ident} not found")


def get[T](session: Session, model: type[T], ident: object) -> T:
    obj = session.get(model, ident)
    if obj is None:
        raise NotFound(model.__name__, ident)
    return obj


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# --- gate --------------------------------------------------------------------


@dataclass
class GateInData:
    plate_number: str
    driver_name: str
    driver_passport: str
    trailer_number: str | None = None
    country: str | None = None
    driver_phone: str | None = None
    tir_carnet_number: str | None = None
    cmr_number: str | None = None
    cargo_description: str | None = None


def gate_in(session: Session, data: GateInData) -> Visit:
    plate = data.plate_number.strip().upper()
    if not plate:
        raise DomainError("invalid_plate", "Plate number is required")

    vehicle = session.scalar(select(Vehicle).where(Vehicle.plate_number == plate))
    if vehicle is None:
        vehicle = Vehicle(plate_number=plate)
        session.add(vehicle)
    vehicle.trailer_number = data.trailer_number or vehicle.trailer_number
    vehicle.country = (data.country or vehicle.country or "").upper()[:2] or None

    on_site = session.scalar(
        select(Visit).where(Visit.vehicle == vehicle, Visit.status.not_in([VisitStatus.exited]))
    )
    if on_site is not None:
        raise DomainError("already_on_site", f"Vehicle {plate} is already in visit {on_site.id}")

    passport = data.driver_passport.strip().upper()
    driver = session.scalar(select(Driver).where(Driver.passport_number == passport))
    if driver is None:
        driver = Driver(passport_number=passport, full_name=data.driver_name.strip())
        session.add(driver)
    driver.full_name = data.driver_name.strip() or driver.full_name
    driver.phone = data.driver_phone or driver.phone

    visit = Visit(
        vehicle=vehicle,
        driver=driver,
        tir_carnet_number=data.tir_carnet_number,
        cmr_number=data.cmr_number,
        cargo_description=data.cargo_description,
    )
    session.add(visit)
    session.flush()
    return visit


def request_exit(session: Session, visit_id: int) -> Visit:
    """Check exit rules, add the parking charge and move the visit to payment."""
    visit = get(session, Visit, visit_id)
    if visit.status not in (VisitStatus.registered, VisitStatus.parked):
        raise DomainError("bad_status", f"Cannot request exit from status {visit.status.value}")

    for op in visit.operations:
        if op.status in OPEN_OPERATION:
            raise DomainError("open_operation", f"Operation {op.id} is not finished")
        if op.status == OperationStatus.completed and not op.customs_cleared:
            raise DomainError("customs_pending", f"Operation {op.id} is not customs cleared")

    days = parking_days(visit)
    if days > 0:
        _add_charge(session, visit, PARKING_TARIFF, Decimal(days), f"Parking, {days} day(s)")

    visit.status = (
        VisitStatus.cleared if visit.total_due == 0 else VisitStatus.awaiting_payment
    )
    session.flush()
    return visit


def gate_out(session: Session, visit_id: int) -> Visit:
    visit = get(session, Visit, visit_id)
    if visit.status != VisitStatus.cleared:
        raise DomainError("not_cleared", "Visit must be cleared before the vehicle leaves")
    visit.slot = None
    visit.status = VisitStatus.exited
    visit.exited_at = utcnow()
    session.flush()
    return visit


def parking_days(visit: Visit) -> int:
    """Started days on site. Any stay counts as at least one day."""
    end = as_utc(visit.exited_at) if visit.exited_at else utcnow()
    seconds = (end - as_utc(visit.arrived_at)).total_seconds()
    return max(1, math.ceil(seconds / 86400))


# --- yard --------------------------------------------------------------------


def free_slots(session: Session) -> list[YardSlot]:
    taken = select(Visit.slot_id).where(Visit.slot_id.is_not(None))
    return list(
        session.scalars(select(YardSlot).where(YardSlot.id.not_in(taken)).order_by(YardSlot.id))
    )


def park(session: Session, visit_id: int, slot_id: int | None = None) -> Visit:
    """Put the vehicle in a slot. Without `slot_id` the first free slot is used."""
    visit = get(session, Visit, visit_id)
    if visit.status not in (VisitStatus.registered, VisitStatus.parked):
        raise DomainError("bad_status", f"Cannot park a visit in status {visit.status.value}")

    if slot_id is None:
        slots = free_slots(session)
        if not slots:
            raise DomainError("yard_full", "No free yard slots")
        slot = slots[0]
    else:
        slot = get(session, YardSlot, slot_id)
        if slot.visit is not None and slot.visit.id != visit.id:
            raise DomainError("slot_taken", f"Slot {slot.label} is occupied")

    visit.slot = slot
    visit.status = VisitStatus.parked
    session.flush()
    return visit


# --- operations --------------------------------------------------------------


@dataclass
class OperationData:
    type: OperationType
    weight_kg: Decimal
    cargo_owner: str | None = None
    notes: str | None = None
    target_visit_id: int | None = None
    stock_lot_id: int | None = None


def plan_operation(session: Session, visit_id: int, data: OperationData) -> Operation:
    visit = get(session, Visit, visit_id)
    if visit.status not in ON_SITE:
        raise DomainError("bad_status", f"Visit {visit.id} is not on site")
    if data.weight_kg <= 0:
        raise DomainError("invalid_weight", "Weight must be positive")

    op = Operation(
        visit=visit,
        type=data.type,
        weight_kg=data.weight_kg,
        cargo_owner=data.cargo_owner,
        notes=data.notes,
    )
    session.add(op)

    if data.type == OperationType.direct_transfer:
        if data.target_visit_id is None:
            raise DomainError("target_required", "Direct transfer needs a receiving vehicle")
        if data.target_visit_id == visit.id:
            raise DomainError("same_vehicle", "Cannot transfer goods to the same vehicle")
        target = get(session, Visit, data.target_visit_id)
        if target.status not in ON_SITE:
            raise DomainError("bad_status", f"Receiving visit {target.id} is not on site")
        op.target_visit = target
    elif data.type == OperationType.warehouse_load:
        if data.stock_lot_id is None:
            raise DomainError("lot_required", "Warehouse load needs a stock lot")
        lot = get(session, StockLot, data.stock_lot_id)
        if lot.quantity_kg < data.weight_kg:
            raise DomainError("insufficient_stock", f"Lot {lot.id} has {lot.quantity_kg} kg")
        op.stock_lot = lot
    elif data.type == OperationType.warehouse_unload and not data.cargo_owner:
        raise DomainError("owner_required", "Warehouse unload needs a cargo owner")

    session.flush()
    return op


def start_operation(session: Session, operation_id: int) -> Operation:
    op = get(session, Operation, operation_id)
    if op.status != OperationStatus.planned:
        raise DomainError("bad_status", f"Operation is {op.status.value}")
    if op.visit.status != VisitStatus.parked:
        raise DomainError("not_parked", "Vehicle must be parked before the operation starts")
    op.status = OperationStatus.in_progress
    op.visit.status = VisitStatus.in_operation
    session.flush()
    return op


def complete_operation(session: Session, operation_id: int) -> Operation:
    op = get(session, Operation, operation_id)
    if op.status != OperationStatus.in_progress:
        raise DomainError("bad_status", f"Operation is {op.status.value}")

    if op.type == OperationType.warehouse_unload:
        lot = StockLot(
            owner=op.cargo_owner or "",
            description=op.visit.cargo_description or f"From {op.visit.vehicle.plate_number}",
            quantity_kg=op.weight_kg,
        )
        session.add(lot)
        op.stock_lot = lot
    elif op.type == OperationType.warehouse_load:
        assert op.stock_lot is not None
        if op.stock_lot.quantity_kg < op.weight_kg:
            raise DomainError("insufficient_stock", f"Lot {op.stock_lot.id} is short")
        op.stock_lot.quantity_kg -= op.weight_kg

    op.status = OperationStatus.completed
    op.completed_at = utcnow()
    tonnes = (op.weight_kg / 1000).quantize(Decimal("0.001"))
    _add_charge(session, op.visit, op.type.value, tonnes, f"{op.type.value}, op #{op.id}")

    if not any(o.status == OperationStatus.in_progress for o in op.visit.operations):
        op.visit.status = VisitStatus.parked
    session.flush()
    return op


def cancel_operation(session: Session, operation_id: int) -> Operation:
    op = get(session, Operation, operation_id)
    if op.status not in OPEN_OPERATION:
        raise DomainError("bad_status", f"Operation is {op.status.value}")
    was_running = op.status == OperationStatus.in_progress
    op.status = OperationStatus.cancelled
    if was_running and not any(
        o.status == OperationStatus.in_progress for o in op.visit.operations
    ):
        op.visit.status = VisitStatus.parked
    session.flush()
    return op


def set_customs_cleared(session: Session, operation_id: int, cleared: bool = True) -> Operation:
    """Customs can be done before or after the physical operation."""
    op = get(session, Operation, operation_id)
    if op.status == OperationStatus.cancelled:
        raise DomainError("bad_status", "Operation is cancelled")
    op.customs_cleared = cleared
    session.flush()
    return op


# --- weighbridge -------------------------------------------------------------


def weigh(session: Session, visit_id: int, gross_kg: Decimal, tare_kg: Decimal) -> WeighTicket:
    visit = get(session, Visit, visit_id)
    if visit.status not in ON_SITE:
        raise DomainError("bad_status", f"Visit {visit.id} is not on site")
    if gross_kg <= 0 or tare_kg < 0 or tare_kg > gross_kg:
        raise DomainError("invalid_weight", "Need gross > 0 and 0 <= tare <= gross")
    ticket = WeighTicket(visit=visit, gross_kg=gross_kg, tare_kg=tare_kg)
    session.add(ticket)
    _add_charge(session, visit, WEIGHBRIDGE_TARIFF, Decimal(1), "Weighbridge")
    session.flush()
    return ticket


# --- billing -----------------------------------------------------------------


def _add_charge(
    session: Session, visit: Visit, tariff_code: str, quantity: Decimal, description: str
) -> Charge | None:
    tariff = session.get(Tariff, tariff_code)
    if tariff is None:  # no tariff configured means the service is free
        return None
    charge = Charge(
        visit=visit,
        tariff_code=tariff.code,
        description=description,
        quantity=quantity,
        unit_price=tariff.price,
        amount=_money(tariff.price * quantity),
    )
    session.add(charge)
    session.flush()
    return charge


def pay_visit(session: Session, visit_id: int) -> Visit:
    """Mark all open charges as paid. Payment gateways are out of scope for v0.1."""
    visit = get(session, Visit, visit_id)
    now = utcnow()
    for charge in visit.charges:
        if not charge.paid:
            charge.paid = True
            charge.paid_at = now
    if visit.status == VisitStatus.awaiting_payment:
        visit.status = VisitStatus.cleared
    session.flush()
    return visit


# --- queries -----------------------------------------------------------------


def list_visits(
    session: Session, *, on_site_only: bool = False, plate: str | None = None
) -> list[Visit]:
    """Newest first. `plate` matches any part of the plate number, ignoring case."""
    stmt = select(Visit).order_by(Visit.id.desc())
    if on_site_only:
        stmt = stmt.where(Visit.status != VisitStatus.exited)
    plate = (plate or "").strip().upper()
    if plate:
        # plates are stored upper-case; autoescape keeps % and _ literal
        stmt = stmt.join(Visit.vehicle).where(
            Vehicle.plate_number.contains(plate, autoescape=True)
        )
    return list(session.scalars(stmt))


def dashboard_counts(session: Session) -> dict[str, int]:
    rows = session.execute(select(Visit.status, func.count()).group_by(Visit.status)).all()
    counts = {status.value: 0 for status in VisitStatus}
    counts.update({status.value: n for status, n in rows})
    counts["free_slots"] = len(free_slots(session))
    return counts

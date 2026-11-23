"""Pydantic schemas for the REST API."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from karvon.models import OperationStatus, OperationType, Role, TariffUnit, VisitStatus


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class GateIn(BaseModel):
    plate_number: str = Field(min_length=1, max_length=32, examples=["01A123BC"])
    trailer_number: str | None = Field(default=None, max_length=32)
    country: str | None = Field(default=None, max_length=2, examples=["UZ"])
    driver_name: str = Field(min_length=1, max_length=128)
    driver_passport: str = Field(min_length=1, max_length=32)
    driver_phone: str | None = Field(default=None, max_length=32)
    tir_carnet_number: str | None = Field(default=None, max_length=32)
    cmr_number: str | None = Field(default=None, max_length=32)
    cargo_description: str | None = None


class ParkIn(BaseModel):
    slot_id: int | None = Field(default=None, description="Omit to take the first free slot")


class OperationIn(BaseModel):
    type: OperationType
    weight_kg: Decimal = Field(gt=0)
    cargo_owner: str | None = None
    notes: str | None = None
    target_visit_id: int | None = None
    stock_lot_id: int | None = None


class CustomsIn(BaseModel):
    cleared: bool = True


class WeighIn(BaseModel):
    gross_kg: Decimal = Field(gt=0)
    tare_kg: Decimal = Field(ge=0)


class VehicleOut(ORM):
    id: int
    plate_number: str
    trailer_number: str | None
    country: str | None


class DriverOut(ORM):
    id: int
    full_name: str
    passport_number: str
    phone: str | None


class SlotOut(ORM):
    id: int
    code: str
    label: str


class OperationOut(ORM):
    id: int
    visit_id: int
    type: OperationType
    status: OperationStatus
    weight_kg: Decimal
    cargo_owner: str | None
    notes: str | None
    customs_cleared: bool
    target_visit_id: int | None
    stock_lot_id: int | None
    created_at: datetime
    completed_at: datetime | None


class WeighTicketOut(ORM):
    id: int
    visit_id: int
    gross_kg: Decimal
    tare_kg: Decimal
    net_kg: Decimal
    created_at: datetime


class ChargeOut(ORM):
    id: int
    tariff_code: str
    description: str
    quantity: Decimal
    unit_price: Decimal
    amount: Decimal
    paid: bool


class VisitOut(ORM):
    id: int
    status: VisitStatus
    vehicle: VehicleOut
    driver: DriverOut
    slot: SlotOut | None
    tir_carnet_number: str | None
    cmr_number: str | None
    cargo_description: str | None
    arrived_at: datetime
    exited_at: datetime | None
    total_due: Decimal


class VisitDetailOut(VisitOut):
    operations: list[OperationOut]
    weigh_tickets: list[WeighTicketOut]
    charges: list[ChargeOut]


class ZoneOut(ORM):
    id: int
    code: str
    name: str
    slots: list[SlotOut]


class StockLotOut(ORM):
    id: int
    owner: str
    description: str
    quantity_kg: Decimal
    created_at: datetime


class TariffOut(ORM):
    code: str
    name: str
    unit: TariffUnit
    price: Decimal


class UserOut(ORM):
    id: int
    username: str
    full_name: str
    role: Role
    active: bool


class AuditEventOut(ORM):
    id: int
    at: datetime
    username: str
    action: str
    visit_id: int | None
    details: str | None


class ErrorOut(BaseModel):
    code: str
    message: str

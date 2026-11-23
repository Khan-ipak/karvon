"""SQLAlchemy models for the terminal domain."""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from karvon.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    """SQLite drops tzinfo; treat naive datetimes as UTC."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class VisitStatus(enum.StrEnum):
    registered = "registered"
    parked = "parked"
    in_operation = "in_operation"
    awaiting_payment = "awaiting_payment"
    cleared = "cleared"
    exited = "exited"


class OperationType(enum.StrEnum):
    direct_transfer = "direct_transfer"
    warehouse_unload = "warehouse_unload"
    warehouse_load = "warehouse_load"


class OperationStatus(enum.StrEnum):
    planned = "planned"
    in_progress = "in_progress"
    completed = "completed"
    cancelled = "cancelled"


class Role(enum.StrEnum):
    """Who does what at the terminal. See `karvon.auth.PERMISSIONS`."""

    admin = "admin"
    operator = "operator"  # gate / central operations
    yard = "yard"  # yard manager
    customs = "customs"  # customs broker
    warehouse = "warehouse"  # warehouse manager
    accountant = "accountant"


class TariffUnit(enum.StrEnum):
    day = "day"
    tonne = "tonne"
    piece = "piece"


def _enum(cls: type[enum.Enum]) -> Enum:
    return Enum(cls, native_enum=False, length=32)


class Vehicle(Base):
    __tablename__ = "vehicles"

    id: Mapped[int] = mapped_column(primary_key=True)
    plate_number: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    trailer_number: Mapped[str | None] = mapped_column(String(32))
    country: Mapped[str | None] = mapped_column(String(2))

    visits: Mapped[list[Visit]] = relationship(back_populates="vehicle")


class Driver(Base):
    __tablename__ = "drivers"

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(128))
    passport_number: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(32))

    visits: Mapped[list[Visit]] = relationship(back_populates="driver")


class YardZone(Base):
    __tablename__ = "yard_zones"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True)
    name: Mapped[str] = mapped_column(String(64))

    slots: Mapped[list[YardSlot]] = relationship(back_populates="zone", order_by="YardSlot.code")


class YardSlot(Base):
    __tablename__ = "yard_slots"
    __table_args__ = (UniqueConstraint("zone_id", "code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    zone_id: Mapped[int] = mapped_column(ForeignKey("yard_zones.id"))
    code: Mapped[str] = mapped_column(String(16))

    zone: Mapped[YardZone] = relationship(back_populates="slots")
    visit: Mapped[Visit | None] = relationship(back_populates="slot", uselist=False)

    @property
    def label(self) -> str:
        return f"{self.zone.code}-{self.code}"


class Visit(Base):
    """One stay of one truck at the terminal, from gate-in to gate-out."""

    __tablename__ = "visits"

    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"))
    driver_id: Mapped[int] = mapped_column(ForeignKey("drivers.id"))
    slot_id: Mapped[int | None] = mapped_column(ForeignKey("yard_slots.id"), unique=True)
    status: Mapped[VisitStatus] = mapped_column(
        _enum(VisitStatus), default=VisitStatus.registered, index=True
    )
    tir_carnet_number: Mapped[str | None] = mapped_column(String(32))
    cmr_number: Mapped[str | None] = mapped_column(String(32))
    cargo_description: Mapped[str | None] = mapped_column(Text)
    arrived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    exited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    vehicle: Mapped[Vehicle] = relationship(back_populates="visits")
    driver: Mapped[Driver] = relationship(back_populates="visits")
    slot: Mapped[YardSlot | None] = relationship(back_populates="visit")
    operations: Mapped[list[Operation]] = relationship(
        back_populates="visit", order_by="Operation.id", foreign_keys="Operation.visit_id"
    )
    weigh_tickets: Mapped[list[WeighTicket]] = relationship(
        back_populates="visit", order_by="WeighTicket.id"
    )
    charges: Mapped[list[Charge]] = relationship(back_populates="visit", order_by="Charge.id")
    audit_events: Mapped[list[AuditEvent]] = relationship(
        back_populates="visit", order_by="AuditEvent.id"
    )

    @property
    def total_due(self) -> Decimal:
        return sum((c.amount for c in self.charges if not c.paid), Decimal("0.00"))


class StockLot(Base):
    """Goods stored in the warehouse, owned by a cargo owner."""

    __tablename__ = "stock_lots"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text)
    quantity_kg: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Operation(Base):
    __tablename__ = "operations"

    id: Mapped[int] = mapped_column(primary_key=True)
    visit_id: Mapped[int] = mapped_column(ForeignKey("visits.id"), index=True)
    type: Mapped[OperationType] = mapped_column(_enum(OperationType))
    status: Mapped[OperationStatus] = mapped_column(
        _enum(OperationStatus), default=OperationStatus.planned
    )
    weight_kg: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    cargo_owner: Mapped[str | None] = mapped_column(String(128))
    notes: Mapped[str | None] = mapped_column(Text)
    customs_cleared: Mapped[bool] = mapped_column(Boolean, default=False)
    # direct_transfer: the truck receiving the goods
    target_visit_id: Mapped[int | None] = mapped_column(ForeignKey("visits.id"))
    # warehouse_unload creates a lot, warehouse_load consumes one
    stock_lot_id: Mapped[int | None] = mapped_column(ForeignKey("stock_lots.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    visit: Mapped[Visit] = relationship(back_populates="operations", foreign_keys=[visit_id])
    target_visit: Mapped[Visit | None] = relationship(foreign_keys=[target_visit_id])
    stock_lot: Mapped[StockLot | None] = relationship()


class WeighTicket(Base):
    __tablename__ = "weigh_tickets"

    id: Mapped[int] = mapped_column(primary_key=True)
    visit_id: Mapped[int] = mapped_column(ForeignKey("visits.id"), index=True)
    gross_kg: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    tare_kg: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    visit: Mapped[Visit] = relationship(back_populates="weigh_tickets")

    @property
    def net_kg(self) -> Decimal:
        return self.gross_kg - self.tare_kg


class Tariff(Base):
    __tablename__ = "tariffs"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    unit: Mapped[TariffUnit] = mapped_column(_enum(TariffUnit))
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))


class Charge(Base):
    __tablename__ = "charges"

    id: Mapped[int] = mapped_column(primary_key=True)
    visit_id: Mapped[int] = mapped_column(ForeignKey("visits.id"), index=True)
    tariff_code: Mapped[str] = mapped_column(ForeignKey("tariffs.code"))
    description: Mapped[str] = mapped_column(String(255))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    paid: Mapped[bool] = mapped_column(Boolean, default=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    visit: Mapped[Visit] = relationship(back_populates="charges")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(128))
    role: Mapped[Role] = mapped_column(_enum(Role))
    password_hash: Mapped[str] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UserSession(Base):
    """A login. The cookie holds a random token; only its SHA-256 is stored."""

    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship()


class AuditEvent(Base):
    """Who did what and when. `username` is copied so history survives user changes."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    username: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(32), index=True)
    visit_id: Mapped[int | None] = mapped_column(ForeignKey("visits.id"), index=True)
    details: Mapped[str | None] = mapped_column(Text)

    visit: Mapped[Visit | None] = relationship(back_populates="audit_events")

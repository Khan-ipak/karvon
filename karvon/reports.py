"""Management reports for a date range: throughput, dwell time and revenue.

Days are days in the terminal's time zone (KARVON_TIMEZONE). Ranges are small (one
terminal, at most a year), so rows are loaded and summed in Python, which keeps
the queries the same on SQLite and PostgreSQL.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, tzinfo
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from karvon.models import (
    Charge,
    Operation,
    OperationStatus,
    OperationType,
    Tariff,
    Visit,
    as_utc,
    utcnow,
)
from karvon.services import DomainError

DEFAULT_DAYS = 30
MAX_DAYS = 366
ZERO = Decimal("0.00")


@dataclass
class DayRow:
    day: date
    arrivals: int = 0
    exits: int = 0
    revenue: Decimal = ZERO


@dataclass
class OperationRow:
    type: OperationType
    count: int = 0
    tonnes: Decimal = Decimal("0.000")


@dataclass
class RevenueRow:
    tariff_code: str
    name: str
    amount: Decimal = ZERO
    paid: Decimal = ZERO

    @property
    def unpaid(self) -> Decimal:
        return self.amount - self.paid


@dataclass
class Dwell:
    """Time on site of the vehicles that left during the period."""

    count: int
    average_hours: float
    median_hours: float
    max_hours: float


@dataclass
class Report:
    start: date
    end: date
    days: list[DayRow]
    operations: list[OperationRow]
    revenue: list[RevenueRow]
    dwell: Dwell | None
    arrivals: int = 0
    exits: int = 0
    revenue_total: Decimal = ZERO
    paid_total: Decimal = ZERO
    # Largest daily values, for drawing bars
    peak_visits: int = field(default=0, repr=False)
    peak_revenue: Decimal = field(default=ZERO, repr=False)


def period(start: date | None, end: date | None, today: date | None = None) -> tuple[date, date]:
    """Fill in missing dates (the last 30 days) and check the range."""
    end = end or today or utcnow().date()
    start = start or end - timedelta(days=DEFAULT_DAYS - 1)
    if start > end:
        raise DomainError("bad_period", "The start date is after the end date")
    if (end - start).days >= MAX_DAYS:
        raise DomainError("period_too_long", f"Choose at most {MAX_DAYS} days")
    return start, end


def today(tz: tzinfo = UTC) -> date:
    return utcnow().astimezone(tz).date()


def _bound(day: date, tz: tzinfo) -> datetime:
    """Local midnight as naive UTC: stored datetimes are UTC, and SQLite keeps them naive."""
    local = datetime(day.year, day.month, day.day, tzinfo=tz)
    return local.astimezone(UTC).replace(tzinfo=None)


def build(session: Session, start: date, end: date, tz: tzinfo = UTC) -> Report:
    lo, hi = _bound(start, tz), _bound(end + timedelta(days=1), tz)

    def local_day(value: datetime) -> date:
        return as_utc(value).astimezone(tz).date()

    days = {
        start + timedelta(days=i): DayRow(start + timedelta(days=i))
        for i in range((end - start).days + 1)
    }

    def in_range(column):
        return (column >= lo) & (column < hi)

    for arrived in session.scalars(select(Visit.arrived_at).where(in_range(Visit.arrived_at))):
        days[local_day(arrived)].arrivals += 1

    hours = []
    left = session.execute(
        select(Visit.arrived_at, Visit.exited_at).where(in_range(Visit.exited_at))
    )
    for arrived, exited in left:
        days[local_day(exited)].exits += 1
        hours.append((as_utc(exited) - as_utc(arrived)).total_seconds() / 3600)
    dwell = None
    if hours:
        dwell = Dwell(
            count=len(hours),
            average_hours=round(sum(hours) / len(hours), 1),
            median_hours=round(statistics.median(hours), 1),
            max_hours=round(max(hours), 1),
        )

    ops = {t: OperationRow(t) for t in OperationType}
    completed = session.execute(
        select(Operation.type, Operation.weight_kg).where(
            Operation.status == OperationStatus.completed, in_range(Operation.completed_at)
        )
    )
    for op_type, weight in completed:
        ops[op_type].count += 1
        ops[op_type].tonnes += (weight / 1000).quantize(Decimal("0.001"))

    names = {code: name for code, name in session.execute(select(Tariff.code, Tariff.name))}
    revenue: dict[str, RevenueRow] = {}
    for charge in session.scalars(select(Charge).where(in_range(Charge.created_at))):
        row = revenue.setdefault(
            charge.tariff_code,
            RevenueRow(charge.tariff_code, names.get(charge.tariff_code, charge.tariff_code)),
        )
        row.amount += charge.amount
        if charge.paid:
            row.paid += charge.amount
        days[local_day(charge.created_at)].revenue += charge.amount

    day_rows = list(days.values())
    revenue_rows = sorted(revenue.values(), key=lambda r: r.amount, reverse=True)
    return Report(
        start=start,
        end=end,
        days=day_rows,
        operations=list(ops.values()),
        revenue=revenue_rows,
        dwell=dwell,
        arrivals=sum(d.arrivals for d in day_rows),
        exits=sum(d.exits for d in day_rows),
        revenue_total=sum((r.amount for r in revenue_rows), ZERO),
        paid_total=sum((r.paid for r in revenue_rows), ZERO),
        peak_visits=max((max(d.arrivals, d.exits) for d in day_rows), default=0),
        peak_revenue=max((d.revenue for d in day_rows), default=ZERO),
    )

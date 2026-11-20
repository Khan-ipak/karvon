"""Demo data: a small border terminal with a few trucks in different stages.

Run with `python -m karvon.seed`, or set KARVON_SEED_DEMO=1 (default) to seed an
empty database on startup.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from karvon import services
from karvon.models import OperationType, Tariff, TariffUnit, Visit, YardSlot, YardZone, utcnow

TARIFFS = [
    ("parking_day", "TIR parking, per day", TariffUnit.day, "15.00"),
    ("weighbridge", "Weighbridge, per weighing", TariffUnit.piece, "10.00"),
    ("direct_transfer", "Direct transfer truck to truck, per tonne", TariffUnit.tonne, "4.00"),
    ("warehouse_unload", "Warehouse unloading, per tonne", TariffUnit.tonne, "5.00"),
    ("warehouse_load", "Warehouse loading, per tonne", TariffUnit.tonne, "5.00"),
]

ZONES = [("A", "TIR parking A", 12), ("B", "TIR parking B", 12), ("W", "Warehouse ramp", 6)]


def seed_reference_data(session: Session) -> None:
    if session.scalar(select(Tariff).limit(1)) is None:
        for code, name, unit, price in TARIFFS:
            session.add(Tariff(code=code, name=name, unit=unit, price=Decimal(price)))
    if session.scalar(select(YardZone).limit(1)) is None:
        for code, name, size in ZONES:
            zone = YardZone(code=code, name=name)
            zone.slots = [YardSlot(code=f"{i:02d}") for i in range(1, size + 1)]
            session.add(zone)
    session.flush()


def seed_demo_visits(session: Session) -> None:
    if session.scalar(select(Visit).limit(1)) is not None:
        return
    g = services.GateInData

    # 1. Waiting at the gate area, not parked yet.
    services.gate_in(
        session,
        g(
            plate_number="01A777AA",
            country="UZ",
            driver_name="Sardor Karimov",
            driver_passport="AA1234567",
            cargo_description="Flour, 20 t",
        ),
    )

    # 2. Afghan truck, parked two days, unloading to the warehouse in progress.
    afghan = services.gate_in(
        session,
        g(
            plate_number="KBL-4521",
            country="AF",
            driver_name="Ahmad Noori",
            driver_passport="P0987654",
            tir_carnet_number="XK-55102231",
            cargo_description="Dried fruit, 18 t",
        ),
    )
    afghan.arrived_at = utcnow() - timedelta(days=2, hours=3)
    services.park(session, afghan.id)
    op = services.plan_operation(
        session,
        afghan.id,
        services.OperationData(
            type=OperationType.warehouse_unload,
            weight_kg=Decimal("18000"),
            cargo_owner="Termez Fruit Trade LLC",
        ),
    )
    services.set_customs_cleared(session, op.id)
    services.start_operation(session, op.id)

    # 3. Pakistani truck and a local truck, ready for direct transfer.
    sender = services.gate_in(
        session,
        g(
            plate_number="LHR-8830",
            country="PK",
            driver_name="Imran Shah",
            driver_passport="PK7712093",
            tir_carnet_number="XK-55109987",
            cargo_description="Textile, 12 t",
        ),
    )
    receiver = services.gate_in(
        session,
        g(
            plate_number="30B456CD",
            country="UZ",
            driver_name="Jasur Rakhimov",
            driver_passport="AB7654321",
        ),
    )
    services.park(session, sender.id)
    services.park(session, receiver.id)
    services.plan_operation(
        session,
        sender.id,
        services.OperationData(
            type=OperationType.direct_transfer,
            weight_kg=Decimal("12000"),
            cargo_owner="Silk Road Textiles",
            target_visit_id=receiver.id,
        ),
    )
    session.flush()


def seed(session: Session, *, demo: bool = True) -> None:
    seed_reference_data(session)
    if demo:
        seed_demo_visits(session)


if __name__ == "__main__":
    from karvon.config import Settings
    from karvon.db import make_engine, make_sessionmaker
    from karvon.migrate import upgrade

    settings = Settings()
    engine = make_engine(settings.database_url)
    upgrade(engine)
    with make_sessionmaker(engine)() as s, s.begin():
        seed(s)
    print(f"Seeded {settings.database_url}")

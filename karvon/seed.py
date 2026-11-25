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
from karvon.models import (
    OperationType,
    Role,
    Tariff,
    TariffUnit,
    Visit,
    YardSlot,
    YardZone,
    utcnow,
)

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
            cmr_number="AF-0431726",
            cargo_description="Dried fruit, 18 t",
            sender="Mazar Dry Fruits Co.\nMazar-i-Sharif, Afghanistan",
            consignee="Termez Fruit Trade LLC\nTermez, Uzbekistan",
            loading_place="Mazar-i-Sharif, AF",
            delivery_place="Termez, UZ",
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
            cmr_number="PK-1187245",
            cargo_description="Textile, 12 t",
            sender="Lahore Textile Mills\nLahore, Pakistan",
            consignee="Tashkent Textile House\nTashkent, Uzbekistan",
            loading_place="Lahore, PK",
            delivery_place="Tashkent, UZ",
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
    seed_demo_history(session)


# (plate, country, driver, passport, days ago it left, hours on site, unloaded kg or 0)
PAST_VISITS = [
    ("01H112KA", "UZ", "Ulugbek Nazarov", "AD1100231", 1, 9, 15000),
    ("KDR-1907", "AF", "Gul Mohammad", "P1203398", 2, 41, 22000),
    ("40C908BB", "UZ", "Rustam Aliev", "AE5502117", 3, 5, 0),
    ("ISB-2271", "PK", "Bilal Ahmed", "PK8830142", 4, 66, 19500),
    ("MZR-3310", "AF", "Najib Rahimi", "P2290871", 6, 28, 17000),
    ("01K450TT", "UZ", "Akmal Yusupov", "AC9901443", 8, 12, 0),
    ("KBL-6604", "AF", "Farid Sultani", "P3381120", 10, 50, 20000),
    ("LHE-5512", "PK", "Usman Tariq", "PK9013376", 13, 19, 14000),
]


def seed_demo_history(session: Session) -> None:
    """Vehicles that already came and left, so reports and history have data."""
    from sqlalchemy import update

    from karvon.models import AuditEvent, Charge, Operation, WeighTicket

    now = utcnow().replace(minute=0, second=0, microsecond=0)
    for plate, country, driver, passport, days_ago, hours, kg in PAST_VISITS:
        left = now - timedelta(days=days_ago, hours=3)
        arrived = left - timedelta(hours=hours)
        visit = services.gate_in(
            session,
            services.GateInData(
                plate_number=plate, country=country, driver_name=driver, driver_passport=passport
            ),
        )
        visit.arrived_at = arrived
        services.park(session, visit.id)
        services.weigh(session, visit.id, Decimal(kg + 15000), Decimal(15000))
        if kg:
            op = services.plan_operation(
                session,
                visit.id,
                services.OperationData(
                    type=OperationType.warehouse_unload,
                    weight_kg=Decimal(kg),
                    cargo_owner="Termez Fruit Trade LLC",
                ),
            )
            services.set_customs_cleared(session, op.id)
            services.start_operation(session, op.id)
            services.complete_operation(session, op.id)
        visit.exited_at = left  # parking is charged up to this moment
        services.request_exit(session, visit.id)
        services.pay_visit(session, visit.id)
        services.gate_out(session, visit.id)
        visit.exited_at = left
        session.flush()
        # Move everything that just happened to the visit's own past times.
        for model, column in (
            (Charge, Charge.created_at),
            (Operation, Operation.completed_at),
            (AuditEvent, AuditEvent.at),
            (WeighTicket, WeighTicket.created_at),
        ):
            session.execute(update(model).where(model.visit_id == visit.id).values({column: left}))
        session.execute(
            update(AuditEvent)
            .where(AuditEvent.visit_id == visit.id, AuditEvent.action == "gate_in")
            .values(at=arrived)
        )
        session.execute(update(Charge).where(Charge.visit_id == visit.id).values(paid_at=left))


DEMO_USERS = [
    (Role.admin, "Demo Admin"),
    (Role.operator, "Dilshod (gate)"),
    (Role.yard, "Bekzod (yard)"),
    (Role.customs, "Nodira (customs broker)"),
    (Role.warehouse, "Jasur (warehouse)"),
    (Role.accountant, "Malika (accountant)"),
]


def seed_demo_users(session: Session) -> None:
    """One user per role; the password is the username. Only for an empty user table."""
    from karvon import auth

    if auth.count_users(session):
        return
    for role, name in DEMO_USERS:
        auth.create_user(session, role.value, role.value, role, name, check_length=False)


def seed(session: Session, *, demo: bool = True) -> None:
    seed_reference_data(session)
    # Demo users (password = username) only go with demo trucks into an empty
    # database. A real database upgraded from v0.1.0 never gets them.
    if demo and session.scalar(select(Visit).limit(1)) is None:
        seed_demo_users(session)
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

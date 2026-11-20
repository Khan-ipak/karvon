"""rename v0.1.0 constraints to the naming convention

Databases created by v0.1.0 (metadata.create_all, before migrations) got
PostgreSQL's default constraint names, e.g. `visits_slot_id_key`. Fresh
databases get `uq_visits_slot_id` from `karvon.db.NAMING_CONVENTION`. Later
migrations refer to constraints by name, so legacy names are renamed here.

Only PostgreSQL is touched, and only names that exist, so this is a no-op on
fresh databases. SQLite is meant for development; recreate it if needed.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, PostgreSQL default name, convention name)
RENAMES: list[tuple[str, str, str]] = [
    ("charges", "charges_pkey", "pk_charges"),
    ("charges", "charges_tariff_code_fkey", "fk_charges_tariff_code_tariffs"),
    ("charges", "charges_visit_id_fkey", "fk_charges_visit_id_visits"),
    ("drivers", "drivers_pkey", "pk_drivers"),
    ("operations", "operations_pkey", "pk_operations"),
    ("operations", "operations_stock_lot_id_fkey", "fk_operations_stock_lot_id_stock_lots"),
    ("operations", "operations_target_visit_id_fkey", "fk_operations_target_visit_id_visits"),
    ("operations", "operations_visit_id_fkey", "fk_operations_visit_id_visits"),
    ("stock_lots", "stock_lots_pkey", "pk_stock_lots"),
    ("tariffs", "tariffs_pkey", "pk_tariffs"),
    ("vehicles", "vehicles_pkey", "pk_vehicles"),
    ("visits", "visits_pkey", "pk_visits"),
    ("visits", "visits_driver_id_fkey", "fk_visits_driver_id_drivers"),
    ("visits", "visits_slot_id_fkey", "fk_visits_slot_id_yard_slots"),
    ("visits", "visits_vehicle_id_fkey", "fk_visits_vehicle_id_vehicles"),
    ("visits", "visits_slot_id_key", "uq_visits_slot_id"),
    ("weigh_tickets", "weigh_tickets_pkey", "pk_weigh_tickets"),
    ("weigh_tickets", "weigh_tickets_visit_id_fkey", "fk_weigh_tickets_visit_id_visits"),
    ("yard_slots", "yard_slots_pkey", "pk_yard_slots"),
    ("yard_slots", "yard_slots_zone_id_fkey", "fk_yard_slots_zone_id_yard_zones"),
    ("yard_slots", "yard_slots_zone_id_code_key", "uq_yard_slots_zone_id_code"),
    ("yard_zones", "yard_zones_pkey", "pk_yard_zones"),
    ("yard_zones", "yard_zones_code_key", "uq_yard_zones_code"),
]


def _rename(pairs: list[tuple[str, str, str]]) -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    existing = set(
        bind.execute(
            sa.text(
                "select conrelid::regclass::text, conname from pg_constraint "
                "where connamespace = current_schema()::regnamespace"
            )
        ).all()
    )
    for table, old, new in pairs:
        if (table, old) in existing:
            op.execute(f'ALTER TABLE "{table}" RENAME CONSTRAINT "{old}" TO "{new}"')


def upgrade() -> None:
    _rename(RENAMES)


def downgrade() -> None:
    # Fresh databases already had the convention names, so going back to the
    # PostgreSQL defaults would be wrong for them. Names stay as they are.
    pass

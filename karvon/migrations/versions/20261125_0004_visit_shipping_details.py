"""shipping details on visits, for the CMR consignment note (#12)

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = (
    ("sender", sa.Text()),
    ("consignee", sa.Text()),
    ("loading_place", sa.String(length=128)),
    ("delivery_place", sa.String(length=128)),
)


def upgrade() -> None:
    with op.batch_alter_table("visits") as batch:
        for name, type_ in COLUMNS:
            batch.add_column(sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("visits") as batch:
        for name, _ in reversed(COLUMNS):
            batch.drop_column(name)

"""Bring the database schema up to date. Called on startup; also `python -m karvon.migrate`."""

from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)

MIGRATIONS = Path(__file__).parent / "migrations"

# v0.1.0 created tables with metadata.create_all and had no alembic_version table.
# Its schema is the one in revision 0001, so such databases are stamped, not re-created.
PRE_ALEMBIC_REVISION = "0001"


def alembic_config(engine: Engine) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.set_main_option("sqlalchemy.url", engine.url.render_as_string(hide_password=False))
    return cfg


def upgrade(engine: Engine, revision: str = "head") -> None:
    cfg = alembic_config(engine)
    with engine.begin() as connection:
        cfg.attributes["connection"] = connection
        tables = set(inspect(connection).get_table_names())
        if "visits" in tables and "alembic_version" not in tables:
            log.warning(
                "Database created before migrations; stamping it at %s", PRE_ALEMBIC_REVISION
            )
            command.stamp(cfg, PRE_ALEMBIC_REVISION)
        command.upgrade(cfg, revision)


if __name__ == "__main__":
    from karvon.config import Settings
    from karvon.db import make_engine

    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    upgrade(make_engine(settings.database_url))
    print(f"Database at {make_engine(settings.database_url).url!r} is up to date")

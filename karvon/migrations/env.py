"""Alembic environment. The database URL comes from KARVON_DATABASE_URL (see karvon.config)
unless the caller already set `sqlalchemy.url`, as `karvon.migrate` does."""

from alembic import context
from sqlalchemy import engine_from_config, pool

import karvon.models  # noqa: F401  (registers all tables on Base.metadata)
from karvon.config import Settings
from karvon.db import Base

config = context.config
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", Settings().database_url)

target_metadata = Base.metadata


def _options(url: str) -> dict:
    # SQLite can't ALTER most things in place; batch mode rebuilds the table instead.
    return {"render_as_batch": url.startswith("sqlite"), "compare_type": True}


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **_options(url),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:  # passed in by karvon.migrate
        _run(connection)
        return
    engine = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with engine.connect() as conn:
        _run(conn)


def _run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        **_options(str(connection.engine.url)),
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

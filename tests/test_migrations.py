"""Migrations must build exactly the schema the models describe, and upgrade old databases."""

import os

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

from karvon.db import Base, make_engine
from karvon.migrate import PRE_ALEMBIC_REVISION, alembic_config, upgrade


def _engine(tmp_path, name="m.db"):
    return make_engine(f"sqlite:///{tmp_path / name}")


def _version(engine):
    with engine.connect() as c:
        return c.execute(text("select version_num from alembic_version")).scalar_one()


def _head(engine):
    return ScriptDirectory.from_config(alembic_config(engine)).get_current_head()


def test_migrations_match_models(tmp_path):
    """Fails when a model changed without a migration: run `alembic revision --autogenerate`."""
    engine = _engine(tmp_path)
    upgrade(engine)
    with engine.connect() as c:
        diff = compare_metadata(MigrationContext.configure(c), Base.metadata)
    assert diff == [], diff


def test_upgrade_is_idempotent(tmp_path):
    engine = _engine(tmp_path)
    upgrade(engine)
    upgrade(engine)
    assert _version(engine) == _head(engine)


def test_downgrade_to_base_and_back(tmp_path):
    engine = _engine(tmp_path)
    upgrade(engine)
    cfg = alembic_config(engine)
    with engine.begin() as c:
        cfg.attributes["connection"] = c
        command.downgrade(cfg, "base")
    assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
    upgrade(engine)
    assert "visits" in inspect(engine).get_table_names()


def test_database_from_v010_is_stamped_and_keeps_data(tmp_path, caplog):
    """v0.1.0 used metadata.create_all and had no alembic_version table."""
    engine = _engine(tmp_path)
    Base.metadata.create_all(engine)
    with engine.begin() as c:
        c.execute(text("insert into vehicles (plate_number) values ('KBL-4521')"))

    upgrade(engine)

    assert f"stamping it at {PRE_ALEMBIC_REVISION}" in caplog.text
    assert _version(engine) == _head(engine)
    with engine.connect() as c:
        assert c.execute(text("select plate_number from vehicles")).scalar_one() == "KBL-4521"


PG_URL = os.environ.get("KARVON_TEST_POSTGRES_URL")


@pytest.mark.skipif(not PG_URL, reason="set KARVON_TEST_POSTGRES_URL to a throwaway database")
def test_postgres_v010_constraint_names_are_normalized(caplog):
    """A v0.1.0 PostgreSQL database ends up with exactly the schema of a fresh one.

    WARNING: drops and recreates the `public` schema of KARVON_TEST_POSTGRES_URL.
    """
    engine = make_engine(PG_URL)
    with engine.begin() as c:
        c.execute(text("drop schema public cascade"))
        c.execute(text("create schema public"))

    # Simulate v0.1.0: the current schema, but with PostgreSQL's default names
    # and no alembic_version table.
    upgrade(engine)
    renames = ScriptDirectory.from_config(alembic_config(engine)).get_revision("0002").module
    with engine.begin() as c:
        for table, old, new in renames.RENAMES:
            c.execute(text(f'alter table "{table}" rename constraint "{new}" to "{old}"'))
        c.execute(text("drop table alembic_version"))

    upgrade(engine)

    assert "stamping it at 0001" in caplog.text
    assert _version(engine) == _head(engine)
    with engine.connect() as c:
        names = set(
            c.execute(
                text(
                    "select conname from pg_constraint"
                    " where connamespace = 'public'::regnamespace"
                )
            ).scalars()
        )
        diff = compare_metadata(MigrationContext.configure(c), Base.metadata)
    assert {new for _, _, new in renames.RENAMES} <= names
    assert not {old for _, old, _ in renames.RENAMES} & names
    assert diff == [], diff

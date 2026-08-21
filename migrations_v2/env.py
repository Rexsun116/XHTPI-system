"""Isolated V2 Alembic environment; explicit URL is mandatory."""

import os
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

from v2.models import db


config = context.config
url = os.environ.get("XHTPI_V2_DATABASE_URL")
if not url:
    raise RuntimeError("XHTPI_V2_DATABASE_URL is required for V2 migrations")
config.set_main_option("sqlalchemy.url", url)
target_metadata = db.metadata


def _guard_url():
    real_v1 = (Path(__file__).resolve().parents[1] / "instance" / "database.db").resolve()
    if url.startswith("sqlite:///"):
        target = Path(url.removeprefix("sqlite:///")).resolve()
        print(f"V2 migration actual database path: {target}")
        if target == real_v1 or real_v1.parent == target.parent:
            raise RuntimeError("V2 lineage refuses the V1 instance directory")


def run_migrations_offline():
    _guard_url()
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    _guard_url()
    connectable = engine_from_config(
        config.get_section(config.config_ini_section), prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        if connection.dialect.name == "sqlite":
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
        # SQLAlchemy 2 connection contexts roll back an uncommitted implicit
        # transaction on exit; commit the Alembic version stamp explicitly.
        connection.commit()


run_migrations_offline() if context.is_offline_mode() else run_migrations_online()

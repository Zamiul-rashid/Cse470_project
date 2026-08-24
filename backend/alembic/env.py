"""Alembic environment.

Migrations run against a *synchronous* SQLite URL derived from the app's async
one. Async Alembic env files are possible but add a layer of complexity that
buys nothing here -- migrations are a one-shot batch job, not a hot path.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from typing import Any

from alembic import context
from sqlalchemy import engine_from_config, event, pool

# backend/alembic/env.py -> alembic -> backend
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import settings  # noqa: E402
from app.models import tables as _tables  # noqa: E402,F401  (registers metadata)
from sqlmodel import SQLModel  # noqa: E402

config = context.config
config.set_main_option("sqlalchemy.url", settings.sync_database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    @event.listens_for(connectable, "connect")
    def _set_sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
        """Mirrors app/core/db.py -- but the placement here is load-bearing.

        Emitting this as ``connection.exec_driver_sql(...)`` inside the
        ``connect()`` block instead is a trap that costs you the whole
        migration. Two SQLite facts collide:

        * ``PRAGMA foreign_keys`` is a silent no-op while a transaction is
          open, so a statement issued on a live Connection may never take
          effect in the first place; and
        * that statement makes SQLAlchemy 2.x *autobegin* a transaction which
          Alembic did not open and therefore never commits. pysqlite runs DDL
          outside SQLAlchemy's transaction, so every ``CREATE TABLE`` lands on
          disk while the ``INSERT INTO alembic_version`` that follows is rolled
          back at block exit -- a fully built database that Alembic believes is
          empty, so the *next* ``alembic upgrade head`` dies on "table campuses
          already exists" and the app never comes up again.

        Listening for ``connect`` runs the PRAGMA on the raw DBAPI connection
        before any transaction can exist, which is both effective and inert.
        """
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # SQLite cannot ALTER most things; batch mode rebuilds the table.
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

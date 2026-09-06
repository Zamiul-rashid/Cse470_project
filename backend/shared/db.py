"""Async engine, session factory and the SQLite PRAGMAs that matter."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=False,
    future=True,
    # SQLite + aiosqlite: a modest pool is plenty and keeps WAL contention low.
    pool_pre_ping=True,
)


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
    """Applied to every new connection.

    * ``foreign_keys=ON``  -- SQLite ignores FK constraints unless you ask.
    * ``journal_mode=WAL`` -- readers stop blocking the single writer, which is
      what makes concurrent chat + browsing usable.
    * ``busy_timeout``     -- wait rather than raise "database is locked" when
      two writes land together.
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


SessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency. One session per request, rolled back on error."""
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


__all__ = ["SessionLocal", "engine", "get_session"]

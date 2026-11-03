"""Database session plumbing. Only repositories depend on this module."""

from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Annotated, Any

import psycopg
from fastapi import Depends
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings, get_settings

# Constraint names are derived from the table, so migrations stay stable and readable.
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


# libpq takes the session zone from PGTZ, and the server zone is used when that is unset.
# SET TIME ZONE runs after both, so timestamptz values always come back in UTC.
UTC_SESSION_SQL = "SET TIME ZONE 'UTC'"


async def pin_session_to_utc(connection: psycopg.AsyncConnection[Any]) -> None:
    """For a psycopg pool that opens its own connections, such as the job queue."""
    _ = await connection.execute(UTC_SESSION_SQL)
    # SET rolls back with its transaction, so commit it before the pool hands the connection out.
    await connection.commit()


def _pin_dbapi_connection_to_utc(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute(UTC_SESSION_SQL)
    finally:
        cursor.close()
    dbapi_connection.commit()


def create_utc_engine(database_url: str) -> AsyncEngine:
    engine = create_async_engine(database_url)
    event.listen(engine.sync_engine, "connect", _pin_dbapi_connection_to_utc)
    return engine


@lru_cache
def session_factory(database_url: str) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(create_utc_engine(database_url), expire_on_commit=False)


async def get_session(
    settings: Annotated[Settings, Depends(get_settings)],
) -> AsyncIterator[AsyncSession]:
    """One session per request. FastAPI caches it, so modules in one request share a transaction."""
    async with session_factory(settings.database_url)() as session:
        yield session

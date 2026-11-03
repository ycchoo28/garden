"""Shared test fixtures. Database tests run against a real Postgres from GARDEN_DATABASE_URL."""

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.composition import MODULES
from app.config import get_settings
from app.shared.db import session_factory

BACKEND_ROOT = Path(__file__).resolve().parent


@pytest.fixture(scope="session")
def migrated_database() -> Iterator[None]:
    url = get_settings().database_url
    # Tests truncate tables, so they must never point at a dev or stage database.
    if not url.rsplit("/", 1)[-1].endswith("_test"):
        raise pytest.UsageError("GARDEN_DATABASE_URL must name a database ending in _test.")
    command.upgrade(Config(str(BACKEND_ROOT / "alembic.ini")), "heads")
    yield


@pytest_asyncio.fixture
async def clean_database(migrated_database: None) -> AsyncIterator[None]:
    tables = [table.fullname for module in MODULES for table in module.metadata.sorted_tables]
    async with session_factory(get_settings().database_url)() as session:
        _ = await session.execute(text(f"TRUNCATE {', '.join(tables)} CASCADE"))
        await session.commit()
    yield

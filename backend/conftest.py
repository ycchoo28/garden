"""Shared fixtures for every backend test, including the tests inside app/.

Database tests run against a real Postgres from GARDEN_DATABASE_URL.
"""

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.composition import MODULES
from app.config import get_settings
from app.core.users.oidc import get_identity_provider
from app.main import create_app
from app.seed import seed
from app.shared.clock import get_clock
from app.shared.db import session_factory
from tests.fakes import FakeIdentity, MovableClock

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


@pytest.fixture
def identity() -> FakeIdentity:
    return FakeIdentity()


@pytest.fixture
def clock() -> MovableClock:
    return MovableClock(datetime(2026, 11, 3, 9, 0, tzinfo=UTC))


@pytest.fixture
def app(identity: FakeIdentity, clock: MovableClock) -> FastAPI:
    """The application under test, with the fake identity provider and the movable clock."""
    built = create_app()
    built.dependency_overrides[get_identity_provider] = lambda: identity
    built.dependency_overrides[get_clock] = lambda: clock
    return built


@pytest_asyncio.fixture
async def client(clean_database: None, app: FastAPI) -> AsyncIterator[AsyncClient]:
    """A client on freshly seeded personas. Each test starts from the same data."""
    await seed()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http

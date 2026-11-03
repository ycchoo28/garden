"""Shared fixtures: an in-process client with the fake identity provider and a movable clock."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.users.identity import IdentityClaims
from app.core.users.oidc import get_identity_provider
from app.main import create_app
from app.seed import seed
from app.shared.clock import get_clock
from tests.fakes import GOOD_CODE, FakeIdentity, MovableClock


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


async def sign_in(client: AsyncClient, identity: FakeIdentity, subject: str) -> None:
    """Run the browser sign-in as the given persona. The session cookie stays on the client."""
    identity.claims = IdentityClaims(subject=subject, email=subject, name=subject)
    started = await client.get("/auth/login", params={"return_to": "/"}, follow_redirects=False)
    assert started.status_code == 302
    state_value = parse_qs(urlparse(started.headers["location"]).query)["state"][0]
    finished = await client.get(
        "/auth/callback",
        params={"code": GOOD_CODE, "state": state_value},
        follow_redirects=False,
    )
    assert finished.status_code == 302, finished.text

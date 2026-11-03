"""The users module: browser sign-in, the caller's permissions, department scope and sessions."""

from datetime import timedelta

import pytest
from httpx import AsyncClient

from app.core.users.identity import IdentityClaims
from tests.conftest import sign_in
from tests.fakes import FakeIdentity, MovableClock

SESSION_IDLE = timedelta(minutes=31)


@pytest.mark.asyncio
async def test_seeded_persona_signs_in_and_sees_permissions(
    client: AsyncClient, identity: FakeIdentity
) -> None:
    await sign_in(client, identity, "dev.admin@fernhill.example")
    me = await client.get("/api/users/me")
    assert me.status_code == 200
    body = me.json()
    assert body["name"] == "Dev Admin"
    assert body["department"]["code"] == "operations"
    assert "admin" in body["roles"]
    assert "employee" in body["roles"]
    assert {"users.self", "users.directory.view", "audit.view"} <= set(body["permissions"])


@pytest.mark.asyncio
async def test_employee_has_no_directory_or_audit_permission(
    client: AsyncClient, identity: FakeIdentity
) -> None:
    await sign_in(client, identity, "marcus.lee@fernhill.example")
    permissions = set((await client.get("/api/users/me")).json()["permissions"])
    assert "users.self" in permissions
    assert "users.directory.view" not in permissions
    assert "audit.view" not in permissions
    assert (await client.get("/api/users/people")).status_code == 403


@pytest.mark.asyncio
async def test_manager_sees_own_department_and_self_only(
    client: AsyncClient, identity: FakeIdentity
) -> None:
    await sign_in(client, identity, "priya.nair@fernhill.example")
    people = (await client.get("/api/users/people")).json()
    departments = {person["department"]["code"] for person in people}
    assert departments == {"design"}
    assert {person["name"] for person in people} == {"Priya Nair", "Marcus Lee"}


@pytest.mark.asyncio
async def test_finance_sees_every_department(client: AsyncClient, identity: FakeIdentity) -> None:
    await sign_in(client, identity, "lena.fischer@fernhill.example")
    people = (await client.get("/api/users/people")).json()
    assert {person["department"]["code"] for person in people} == {
        "design",
        "engineering",
        "marketing",
        "operations",
    }


@pytest.mark.asyncio
async def test_anonymous_request_is_refused_as_problem(client: AsyncClient) -> None:
    response = await client.get("/api/users/me")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")


@pytest.mark.asyncio
async def test_unknown_account_is_refused(client: AsyncClient, identity: FakeIdentity) -> None:
    identity.claims = IdentityClaims(subject="stranger@elsewhere.example", email="x", name="X")
    started = await client.get("/auth/login", follow_redirects=False)
    state = started.headers["location"].split("state=")[1].split("&")[0]
    finished = await client.get(
        "/auth/callback", params={"code": "good-code", "state": state}, follow_redirects=False
    )
    assert finished.status_code == 403


@pytest.mark.asyncio
async def test_sign_in_state_works_once(client: AsyncClient, identity: FakeIdentity) -> None:
    identity.claims = IdentityClaims(subject="noor.haddad@fernhill.example", email="n", name="N")
    started = await client.get("/auth/login", follow_redirects=False)
    state = started.headers["location"].split("state=")[1].split("&")[0]
    first = await client.get(
        "/auth/callback", params={"code": "good-code", "state": state}, follow_redirects=False
    )
    second = await client.get(
        "/auth/callback", params={"code": "good-code", "state": state}, follow_redirects=False
    )
    assert first.status_code == 302
    assert second.status_code == 401


@pytest.mark.asyncio
async def test_idle_session_ends(
    client: AsyncClient, identity: FakeIdentity, clock: MovableClock
) -> None:
    await sign_in(client, identity, "tomas.berg@fernhill.example")
    clock.advance(SESSION_IDLE)
    response = await client.get("/api/users/me")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_sign_out_ends_the_session(client: AsyncClient, identity: FakeIdentity) -> None:
    await sign_in(client, identity, "sam.okafor@fernhill.example")
    assert (await client.post("/api/users/sign-out")).status_code == 204
    assert (await client.get("/api/users/me")).status_code == 401

"""The confirmation gate, exercised through a test-only route that marks an action as critical."""

import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Annotated

import pytest
import pytest_asyncio
from fastapi import APIRouter, Depends
from httpx import ASGITransport, AsyncClient

from app.core.confirmations import CONFIRMATION_HEADER, confirm_agent_action
from app.core.users import CurrentPerson, current_person, require
from app.core.users.scope import DataScope
from app.main import create_app
from app.shared.clock import get_clock
from app.shared.operations import op
from app.shared.request_login import VIA_AGENT, RequestLogin, current_request_login
from tests.fakes import MovableClock

PERSON_ID = uuid.UUID("0190f0a0-0000-7000-8000-000000000001")
DECIDER = CurrentPerson(
    id=PERSON_ID,
    name="Test Person",
    email="test@example.test",
    department_id=PERSON_ID,
    department_code="design",
    department_name="Design",
    roles=frozenset({"employee"}),
    permissions=frozenset({"users.self", "confirmations.decide"}),
    scope=DataScope(all_departments=False, department_ids=frozenset()),
)


async def _as_agent() -> None:
    _ = current_request_login.set(RequestLogin(via=VIA_AGENT, device="laptop"))


gated = APIRouter()


@gated.post(
    "/test/gated",
    dependencies=[Depends(_as_agent), confirm_agent_action("platform.gate.run")],
    **op("platform.gate.run", "Test-only critical action.", confirm=True),
)
async def gated_action(
    body: dict[str, str],
    person: Annotated[CurrentPerson, require("users.self")],
) -> dict[str, str]:
    return {"ran": body["name"], "for": str(person.id)}


@gated.post(
    "/test/gated-browser",
    dependencies=[confirm_agent_action("platform.gate.run")],
    **op("platform.gate.browse", "Test-only critical action for browsers.", confirm=True),
)
async def gated_browser(body: dict[str, str]) -> dict[str, str]:
    return {"ran": body["name"]}


@pytest_asyncio.fixture
async def gate(clean_database: None, clock: MovableClock) -> AsyncIterator[AsyncClient]:
    app = create_app()
    app.include_router(gated)
    app.dependency_overrides[current_person] = lambda: DECIDER
    app.dependency_overrides[get_clock] = lambda: clock
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http:
        yield http


async def _challenge(gate: AsyncClient, body: dict[str, str]) -> dict[str, str]:
    response = await gate.post("/test/gated", json=body)
    assert response.status_code == 428, response.text
    return response.json()


async def _approve(gate: AsyncClient, confirmation_id: str) -> None:
    decided = await gate.post(
        f"/api/confirmations/{confirmation_id}/decision", json={"decision": "approve"}
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["status"] == "approved"


@pytest.mark.asyncio
async def test_agent_action_without_approval_answers_428_with_the_way_to_approve(
    gate: AsyncClient,
) -> None:
    problem = await _challenge(gate, {"name": "first"})
    assert problem["status"] == 428
    assert problem["confirmation_id"]
    assert problem["confirmation_url"].endswith(f"/confirm/{problem['confirmation_id']}")


@pytest.mark.asyncio
async def test_approved_retry_runs_once_then_is_used(gate: AsyncClient) -> None:
    problem = await _challenge(gate, {"name": "first"})
    await _approve(gate, problem["confirmation_id"])
    headers = {CONFIRMATION_HEADER: problem["confirmation_id"]}
    ran = await gate.post("/test/gated", json={"name": "first"}, headers=headers)
    assert ran.status_code == 200, ran.text
    assert ran.json()["ran"] == "first"
    again = await gate.post("/test/gated", json={"name": "first"}, headers=headers)
    assert again.status_code == 409


@pytest.mark.asyncio
async def test_approval_does_not_carry_over_to_a_different_request(gate: AsyncClient) -> None:
    problem = await _challenge(gate, {"name": "first"})
    await _approve(gate, problem["confirmation_id"])
    changed = await gate.post(
        "/test/gated",
        json={"name": "second"},
        headers={CONFIRMATION_HEADER: problem["confirmation_id"]},
    )
    assert changed.status_code == 403


@pytest.mark.asyncio
async def test_rejected_confirmation_never_runs(gate: AsyncClient) -> None:
    problem = await _challenge(gate, {"name": "first"})
    rejected = await gate.post(
        f"/api/confirmations/{problem['confirmation_id']}/decision",
        json={"decision": "reject"},
    )
    assert rejected.json()["status"] == "rejected"
    retry = await gate.post(
        "/test/gated",
        json={"name": "first"},
        headers={CONFIRMATION_HEADER: problem["confirmation_id"]},
    )
    assert retry.status_code == 403


@pytest.mark.asyncio
async def test_confirmation_expires_after_ten_minutes(
    gate: AsyncClient, clock: MovableClock
) -> None:
    problem = await _challenge(gate, {"name": "first"})
    await _approve(gate, problem["confirmation_id"])
    clock.advance(timedelta(minutes=11))
    retry = await gate.post(
        "/test/gated",
        json={"name": "first"},
        headers={CONFIRMATION_HEADER: problem["confirmation_id"]},
    )
    assert retry.status_code == 403


@pytest.mark.asyncio
async def test_browser_requests_pass_the_gate_without_confirmation(gate: AsyncClient) -> None:
    response = await gate.post("/test/gated-browser", json={"name": "web"})
    assert response.status_code == 200
    assert response.json()["ran"] == "web"


@pytest.mark.asyncio
async def test_owner_reads_a_pending_confirmation(gate: AsyncClient) -> None:
    problem = await _challenge(gate, {"name": "first"})
    other = await gate.get(f"/api/confirmations/{problem['confirmation_id']}")
    assert other.status_code == 200
    assert other.json()["status"] == "pending"
    unknown = await gate.get("/api/confirmations/0190f0a0-0000-7000-8000-0000000000ff")
    assert unknown.status_code == 404

"""The audit trail: a saved change records who made it, in the same transaction as the change."""

from typing import Annotated

import pytest
from fastapi import Depends, FastAPI, Request
from httpx import AsyncClient

from app.core.users import CurrentPerson, current_person
from app.core.users.access import SESSION_COOKIE
from app.core.users.service import UserService, get_user_service
from app.shared.request_login import VIA_AGENT, RequestLogin, current_request_login
from tests.fakes import FakeIdentity
from tests.support import sign_in

PNG_HEADER = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


@pytest.mark.asyncio
async def test_upload_writes_an_audit_row_with_the_actor(
    client: AsyncClient, identity: FakeIdentity
) -> None:
    await sign_in(client, identity, "marcus.lee@fernhill.example")
    upload = await client.post("/api/files", files={"file": ("brief.png", PNG_HEADER, "image/png")})
    assert upload.status_code == 200, upload.text
    await sign_in(client, identity, "lena.fischer@fernhill.example")
    events = (await client.get("/api/audit/events")).json()
    upload_events = [event for event in events if event["operation"] == "files.file.upload"]
    assert len(upload_events) == 1
    assert upload_events[0]["acting_as_agent"] is False
    assert upload_events[0]["summary"].startswith("Uploaded brief.png")


@pytest.mark.asyncio
async def test_agent_login_marks_the_row_as_acting_as_agent(
    app: FastAPI, client: AsyncClient, identity: FakeIdentity
) -> None:
    await sign_in(client, identity, "marcus.lee@fernhill.example")

    async def as_agent(
        request: Request,
        service: Annotated[UserService, Depends(get_user_service)],
    ) -> CurrentPerson:
        # Stand in for a CLI login: the same person, but the request is marked as an agent's.
        person = await service.current_person(
            request.cookies.get(SESSION_COOKIE), request.app.state.roles
        )
        _ = current_request_login.set(RequestLogin(via=VIA_AGENT, device="laptop"))
        return person

    app.dependency_overrides[current_person] = as_agent
    try:
        upload = await client.post(
            "/api/files", files={"file": ("agent.png", PNG_HEADER, "image/png")}
        )
    finally:
        del app.dependency_overrides[current_person]
    assert upload.status_code == 200, upload.text
    await sign_in(client, identity, "lena.fischer@fernhill.example")
    events = (await client.get("/api/audit/events")).json()
    agent_rows = [event for event in events if event["summary"].startswith("Uploaded agent.png")]
    assert len(agent_rows) == 1
    assert agent_rows[0]["acting_as_agent"] is True


@pytest.mark.asyncio
async def test_audit_log_is_refused_to_employees(
    client: AsyncClient, identity: FakeIdentity
) -> None:
    await sign_in(client, identity, "marcus.lee@fernhill.example")
    assert (await client.get("/api/audit/events")).status_code == 403

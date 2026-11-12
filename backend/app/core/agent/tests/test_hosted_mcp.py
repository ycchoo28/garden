"""Hosted MCP behavior through the mounted backend endpoint."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import socket
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from uvicorn import Config, Server

import app.core.agent.api as agent_api
from app.config import get_settings
from app.core.audit.models import AuditEvent
from app.core.users.models import CliLogin, Person, RoleGrant
from app.core.users.service import token_hash
from app.shared.db import session_factory
from tests.fakes import FakeIdentity, MovableClock
from tests.support import bearer, cli_sign_in, sign_in

ENDPOINT = "/api/agent/mcp"
MARCUS = "marcus.lee@fernhill.example"


@pytest_asyncio.fixture
async def hosted_mcp_server(
    app: FastAPI,
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> AsyncIterator[str]:
    """Give the hosted Engine a loopback URL while requests use the mounted public seam."""
    _ = client  # Starting this fixture after client setup ensures the seeded test database exists.
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    listener.setblocking(False)
    host, port = listener.getsockname()
    settings = get_settings().model_copy(
        update={
            "agent_mcp_enabled": True,
            "agent_mcp_api_base_url": f"http://{host}:{port}",
            "agent_mcp_allowed_origins": "https://allowed.example",
            "files_root": tmp_path / "files",
        }
    )
    monkeypatch.setattr(agent_api, "get_settings", lambda: settings)
    monkeypatch.setitem(app.dependency_overrides, get_settings, lambda: settings)
    server = Server(Config(app, lifespan="off", log_level="critical", access_log=False))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        for _ in range(1000):
            if server.started:
                break
            await asyncio.sleep(0.01)
        assert server.started, "The backend loopback API did not start."
        yield f"http://{host}:{port}"
    finally:
        server.should_exit = True
        await task
        listener.close()


@pytest.mark.asyncio
async def test_mounted_mcp_endpoint_uses_real_cli_logins_and_audits_hosted_saves(
    client: AsyncClient,
    identity: FakeIdentity,
    hosted_mcp_server: str,
    clock: MovableClock,
) -> None:
    token = await cli_sign_in(client, identity, MARCUS, device="hosted-agent")
    assert token.startswith("ag_")
    await sign_in(client, identity, MARCUS)
    browser_login = await client.post(
        "/api/agent/my-logins", json={"device": "hosted-agent-web", "expires_in_days": 30}
    )
    assert browser_login.status_code == 201, browser_login.text
    web_token = browser_login.json()["token"]
    assert browser_login.json()["source"] == "web"

    async with session_factory(get_settings().database_url)() as session:
        create_event = await session.scalar(
            select(AuditEvent).where(
                AuditEvent.operation == "agent.my_login.create",
                AuditEvent.target == browser_login.json()["id"],
            )
        )
    assert create_event is not None
    assert "hosted-agent-web" in create_event.summary

    async with AsyncClient(
        base_url=hosted_mcp_server,
        timeout=None,
        headers={"Accept": "application/json, text/event-stream"},
    ) as mcp:
        initialized = await mcp.post(
            ENDPOINT,
            headers=bearer(token),
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-03-26"},
            },
        )
        assert initialized.status_code == 200, initialized.text
        assert initialized.json()["result"]["protocolVersion"] == "2025-03-26"

        listing = await mcp.post(
            ENDPOINT,
            headers=bearer(token),
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        )
        assert listing.status_code == 200, listing.text
        names = {tool["name"] for tool in listing.json()["result"]["tools"]}
        assert {"files_file_read", "files_file_upload", "users_self_read"} <= names
        assert "users_directory_list" not in names
        assert "agent_my_login_create" not in names

        cookie_only = await mcp.post(
            ENDPOINT,
            headers={"Cookie": "garden_session=browser-session"},
            json={"jsonrpc": "2.0", "id": 3, "method": "initialize"},
        )
        assert cookie_only.status_code == 401
        assert cookie_only.headers["www-authenticate"] == "Bearer"
        bad_token = await mcp.post(
            ENDPOINT,
            headers={"Authorization": "Bearer ag_invalid"},
            json={"jsonrpc": "2.0", "id": 4, "method": "initialize"},
        )
        assert bad_token.status_code == 401
        refused_origin = await mcp.post(
            ENDPOINT,
            headers={**bearer(token), "Origin": "https://attacker.example"},
            json={"jsonrpc": "2.0", "id": 5, "method": "initialize"},
        )
        assert refused_origin.status_code == 403
        accepted_origin = await mcp.post(
            ENDPOINT,
            headers={**bearer(token), "Origin": "https://allowed.example"},
            json={"jsonrpc": "2.0", "id": 6, "method": "initialize"},
        )
        assert accepted_origin.status_code == 200

        content = b"hosted upload bytes"
        uploaded = await mcp.post(
            ENDPOINT,
            headers=bearer(web_token),
            json={
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {
                    "name": "files_file_upload",
                    "arguments": {
                        "path": {
                            "filename": "agent.txt",
                            "content_type": "text/plain",
                            "data_base64": base64.b64encode(content).decode("ascii"),
                        }
                    },
                },
            },
        )
        assert uploaded.status_code == 200, uploaded.text
        result = uploaded.json()["result"]
        assert result["isError"] is False
        file_reply = json.loads(result["content"][0]["text"])
        assert file_reply["size_bytes"] == len(content)
        assert file_reply["sha256"] == hashlib.sha256(content).hexdigest()

        async with session_factory(get_settings().database_url)() as session:
            person = await session.scalar(select(Person).where(Person.email == MARCUS))
            event = await session.scalar(
                select(AuditEvent).where(
                    AuditEvent.operation == "files.file.upload",
                    AuditEvent.target == f"file:{file_reply['sha256']}",
                )
            )
            if person is not None:
                session.add(RoleGrant(person_id=person.id, role="manager"))
                await session.commit()
            else:
                pytest.fail("The seeded person is missing.")
        assert event is not None
        assert event.acting_as_agent is True
        assert event.device == "hosted-agent-web"
        assert event.login_created_at is not None
        assert "CLI login source: web" in event.summary

        refreshed = await mcp.post(
            ENDPOINT,
            headers=bearer(token),
            json={"jsonrpc": "2.0", "id": 8, "method": "tools/list"},
        )
        refreshed_names = {tool["name"] for tool in refreshed.json()["result"]["tools"]}
        assert "users_directory_list" in refreshed_names

        refused_create = await mcp.post(
            "/api/agent/my-logins",
            headers=bearer(token),
            json={"device": "agent-made", "expires_in_days": 90},
        )
        assert refused_create.status_code == 403

        clock.advance(timedelta(days=30))
        expired = await mcp.post(
            ENDPOINT,
            headers=bearer(web_token),
            json={"jsonrpc": "2.0", "id": 9, "method": "tools/list"},
        )
        assert expired.status_code == 401
        assert expired.headers["www-authenticate"] == "Bearer"
        clock.advance(-timedelta(days=30))
        revoked = await client.post(f"/api/agent/my-logins/{browser_login.json()['id']}/sign-out")
        assert revoked.status_code == 204
        no_longer_valid = await mcp.post(
            ENDPOINT,
            headers=bearer(web_token),
            json={"jsonrpc": "2.0", "id": 10, "method": "tools/list"},
        )
        assert no_longer_valid.status_code == 401

        default_expiry = await client.post("/api/agent/my-logins", json={"device": "default"})
        long_expiry = await client.post(
            "/api/agent/my-logins", json={"device": "long", "expires_in_days": 365}
        )
        never_expires = await client.post(
            "/api/agent/my-logins", json={"device": "no-expiry", "expires_in_days": None}
        )
        assert (
            default_expiry.status_code
            == long_expiry.status_code
            == never_expires.status_code
            == 201
        )
        default_date = datetime.fromisoformat(default_expiry.json()["expires_at"])
        assert default_date == clock.now() + timedelta(days=90)
        long_date = datetime.fromisoformat(long_expiry.json()["expires_at"])
        assert long_date == clock.now() + timedelta(days=365)
        assert never_expires.json()["expires_at"] is None

        listed_logins = await client.get("/api/agent/my-logins")
        listed = {row["id"]: row for row in listed_logins.json()}
        assert listed[browser_login.json()["id"]]["source"] == "web"
        assert "token" not in listed[browser_login.json()["id"]]

        async with session_factory(get_settings().database_url)() as session:
            row = await session.get(CliLogin, browser_login.json()["id"])
        assert row is not None
        assert row.token_hash == token_hash(web_token)
        assert row.token_hash != web_token
        assert row.expires_at is not None


@pytest.mark.asyncio
async def test_critical_mcp_calls_stream_confirmation_and_disconnect_cancels(
    client: AsyncClient,
    identity: FakeIdentity,
    hosted_mcp_server: str,
    clock: MovableClock,
) -> None:
    clock.current = datetime.now(UTC)
    token = await cli_sign_in(client, identity, MARCUS, device="confirmation-agent")
    await sign_in(client, identity, MARCUS)
    async with AsyncClient(
        base_url=hosted_mcp_server,
        timeout=None,
        headers={"Accept": "application/json, text/event-stream"},
    ) as mcp:
        uploaded = await mcp.post(
            ENDPOINT,
            headers=bearer(token),
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "files_file_upload",
                    "arguments": {
                        "path": {
                            "filename": "confirm.txt",
                            "content_type": "text/plain",
                            "data_base64": base64.b64encode(b"confirm me").decode("ascii"),
                        }
                    },
                },
            },
        )
        sha256 = json.loads(uploaded.json()["result"]["content"][0]["text"])["sha256"]

        async with mcp.stream(
            "POST",
            ENDPOINT,
            headers=bearer(token),
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "files_file_delete",
                    "arguments": {"sha256": sha256},
                },
            },
        ) as response:
            assert response.status_code == 200
            lines = response.aiter_lines().__aiter__()
            events = await _read_sse_messages(lines, 1)
            assert events[0]["method"] == "notifications/message"
            confirmation_id = re.search(
                r"/agent/confirmations/([0-9a-f-]{36})", events[0]["params"]["data"]
            )
            assert confirmation_id is not None
            decided = await client.post(
                f"/api/agent/my-confirmations/{confirmation_id.group(1)}/decision",
                json={"decision": "approve"},
            )
            assert decided.status_code == 200, decided.text
            result = await _read_sse_messages(lines, 1)
            assert result[0]["id"] == 2
            assert result[0]["result"]["isError"] is False, result[0]

        uploaded_again = await mcp.post(
            ENDPOINT,
            headers=bearer(token),
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "files_file_upload",
                    "arguments": {
                        "path": {
                            "filename": "disconnect.txt",
                            "content_type": "text/plain",
                            "data_base64": base64.b64encode(b"keep me").decode("ascii"),
                        }
                    },
                },
            },
        )
        disconnect_sha = json.loads(uploaded_again.json()["result"]["content"][0]["text"])["sha256"]
        async with mcp.stream(
            "POST",
            ENDPOINT,
            headers=bearer(token),
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "files_file_delete",
                    "arguments": {"sha256": disconnect_sha},
                },
            },
        ) as response:
            assert response.status_code == 200
            lines = response.aiter_lines().__aiter__()
            messages = await _read_sse_messages(lines, 1)
            match = re.search(
                r"/agent/confirmations/([0-9a-f-]{36})", messages[0]["params"]["data"]
            )
            assert match is not None
            confirmation_id = match.group(1)

        decided = await client.post(
            f"/api/agent/my-confirmations/{confirmation_id}/decision",
            json={"decision": "approve"},
        )
        assert decided.status_code == 200, decided.text
        confirmation = await client.get(
            f"/api/agent/my-confirmations/{confirmation_id}", headers=bearer(token)
        )
        assert confirmation.json()["status"] == "approved"
        await asyncio.sleep(1.2)
        still_exists = await mcp.post(
            ENDPOINT,
            headers=bearer(token),
            json={
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "files_file_read",
                    "arguments": {"sha256": disconnect_sha},
                },
            },
        )
        assert still_exists.json()["result"]["isError"] is False


@pytest.mark.asyncio
async def test_rejected_and_expired_confirmations_do_not_run_hosted_actions(
    client: AsyncClient,
    identity: FakeIdentity,
    hosted_mcp_server: str,
    clock: MovableClock,
) -> None:
    clock.current = datetime.now(UTC)
    token = await cli_sign_in(client, identity, MARCUS, device="confirmation-outcomes")
    await sign_in(client, identity, MARCUS)

    async with AsyncClient(
        base_url=hosted_mcp_server,
        timeout=None,
        headers={"Accept": "application/json, text/event-stream"},
    ) as mcp:

        async def upload(filename: str) -> str:
            result = await mcp.post(
                ENDPOINT,
                headers=bearer(token),
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "files_file_upload",
                        "arguments": {
                            "path": {
                                "filename": filename,
                                "content_type": "text/plain",
                                "data_base64": base64.b64encode(filename.encode()).decode("ascii"),
                            }
                        },
                    },
                },
            )
            assert result.status_code == 200, result.text
            return json.loads(result.json()["result"]["content"][0]["text"])["sha256"]

        async def delete(sha256: str, decision: str | None) -> dict[str, Any]:
            async with mcp.stream(
                "POST",
                ENDPOINT,
                headers=bearer(token),
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "files_file_delete",
                        "arguments": {"sha256": sha256},
                    },
                },
            ) as response:
                assert response.status_code == 200
                lines = response.aiter_lines().__aiter__()
                notification = (await _read_sse_messages(lines, 1))[0]
                match = re.search(
                    r"/agent/confirmations/([0-9a-f-]{36})", notification["params"]["data"]
                )
                assert match is not None
                if decision is not None:
                    decided = await client.post(
                        f"/api/agent/my-confirmations/{match.group(1)}/decision",
                        json={"decision": decision},
                    )
                    assert decided.status_code == 200, decided.text
                else:
                    clock.advance(timedelta(minutes=11))
                return (await _read_sse_messages(lines, 1))[0]

        rejected_sha = await upload("reject.txt")
        rejected = await delete(rejected_sha, "reject")
        assert rejected["result"]["isError"] is True
        assert "rejected" in rejected["result"]["content"][0]["text"].lower()

        expired_sha = await upload("expire.txt")
        expired = await delete(expired_sha, None)
        assert expired["result"]["isError"] is True
        assert "expired" in expired["result"]["content"][0]["text"].lower()

        for request_id, sha256 in enumerate((rejected_sha, expired_sha), start=3):
            read = await mcp.post(
                ENDPOINT,
                headers=bearer(token),
                json={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "tools/call",
                    "params": {
                        "name": "files_file_read",
                        "arguments": {"sha256": sha256},
                    },
                },
            )
            assert read.json()["result"]["isError"] is False


async def _read_sse_messages(lines: AsyncIterator[str], count: int) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    while len(messages) < count:
        line = await anext(lines)
        if line.startswith("data: "):
            messages.append(json.loads(line.removeprefix("data: ")))
    return messages

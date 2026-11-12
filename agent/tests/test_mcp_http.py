"""Public HTTP behavior for the hosted MCP endpoint."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fake_portal import GOOD_TOKEN, FakePortal
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from garden.engine import Engine, PortalClient
from garden.mcp import HostedMCPSettings, create_streamable_http_app, handle

ENDPOINT = "/api/agent/mcp"
HEADERS = {
    "Authorization": f"Bearer {GOOD_TOKEN}",
    "Accept": "application/json, text/event-stream",
}


def _message(request_id: int, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


@pytest.fixture
def hosted_app(portal: FakePortal) -> Any:
    settings = HostedMCPSettings(
        enabled=True,
        api_base_url=portal.url,
        allowed_origins=frozenset({"https://allowed.example"}),
        files_max_bytes=1024,
    )
    return create_streamable_http_app(lambda: settings)


@pytest_asyncio.fixture
async def hosted_http(hosted_app: Any) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=hosted_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.fixture
def portal() -> Iterator[FakePortal]:
    instance = FakePortal()
    instance.start()
    yield instance
    instance.stop()


@pytest.mark.asyncio
async def test_initialization_and_hosted_tools_match_stdio_except_file_shape(
    hosted_http: httpx.AsyncClient,
    portal: FakePortal,
    tmp_path: Path,
) -> None:
    initialized = await hosted_http.post(
        ENDPOINT,
        headers=HEADERS,
        json=_message(1, "initialize", {"protocolVersion": "2025-03-26"}),
    )
    assert initialized.status_code == 200
    assert initialized.json()["result"]["protocolVersion"] == "2025-03-26"
    assert initialized.json()["result"]["serverInfo"]["name"] == "garden"
    assert initialized.json()["result"]["capabilities"]["tools"]

    hosted = await hosted_http.post(ENDPOINT, headers=HEADERS, json=_message(2, "tools/list"))
    assert hosted.status_code == 200
    stdio_engine = Engine(PortalClient(portal.url, token=GOOD_TOKEN), tmp_path)
    stdio = handle(stdio_engine, _message(3, "tools/list"))
    assert stdio is not None
    hosted_tools = hosted.json()["result"]["tools"]
    stdio_tools = stdio["result"]["tools"]
    assert [tool["name"] for tool in hosted_tools] == [tool["name"] for tool in stdio_tools]
    for hosted_tool, stdio_tool in zip(hosted_tools, stdio_tools, strict=True):
        assert hosted_tool["description"] == stdio_tool["description"]
        assert hosted_tool["annotations"] == stdio_tool["annotations"]
        if hosted_tool["name"] in {"files_file_upload", "knowledge_batch_upload"}:
            file_name = "path"
            assert hosted_tool["inputSchema"]["properties"][file_name] == {
                "type": "object",
                "properties": {
                    "filename": {"type": "string"},
                    "content_type": {"type": "string"},
                    "data_base64": {"type": "string"},
                },
                "required": ["filename", "content_type", "data_base64"],
                "additionalProperties": False,
                "description": "File bytes held by the hosted agent.",
            }
            continue
        assert hosted_tool["inputSchema"] == stdio_tool["inputSchema"]


@pytest.mark.asyncio
async def test_auth_ignores_cookies_and_rejects_bad_tokens_and_origins(
    hosted_http: httpx.AsyncClient,
) -> None:
    request = _message(1, "initialize")
    no_token = await hosted_http.post(
        ENDPOINT,
        headers={"Cookie": "garden_session=browser-session"},
        json=request,
    )
    assert no_token.status_code == 401
    assert no_token.headers["www-authenticate"] == "Bearer"

    bad_token = await hosted_http.post(
        ENDPOINT,
        headers={
            "Authorization": "Bearer ag_invalid",
            "Accept": "application/json, text/event-stream",
        },
        json=request,
    )
    assert bad_token.status_code == 401
    assert bad_token.headers["www-authenticate"] == "Bearer"

    bad_origin = await hosted_http.post(
        ENDPOINT,
        headers={**HEADERS, "Origin": "https://attacker.example"},
        json=request,
    )
    assert bad_origin.status_code == 403

    accepted_origin = await hosted_http.post(
        ENDPOINT,
        headers={**HEADERS, "Origin": "https://allowed.example"},
        json=request,
    )
    assert accepted_origin.status_code == 200
    assert accepted_origin.headers["access-control-allow-origin"] == "https://allowed.example"
    assert accepted_origin.headers["access-control-expose-headers"] == "WWW-Authenticate"
    assert accepted_origin.headers["vary"] == "Origin"
    assert "access-control-allow-credentials" not in accepted_origin.headers

    missing_accept = await hosted_http.post(
        ENDPOINT,
        headers={"Authorization": f"Bearer {GOOD_TOKEN}"},
        json=request,
    )
    assert missing_accept.status_code == 406


@pytest.mark.asyncio
async def test_cors_preflight_is_limited_to_configured_origins_methods_and_headers(
    hosted_http: httpx.AsyncClient,
) -> None:
    preflight_headers = {
        "Origin": "https://allowed.example",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": (
            "authorization, content-type, accept, mcp-protocol-version"
        ),
    }
    allowed = await hosted_http.options(ENDPOINT, headers=preflight_headers)
    assert allowed.status_code == 204
    assert allowed.headers["access-control-allow-origin"] == "https://allowed.example"
    assert allowed.headers["access-control-allow-methods"] == "POST"
    allowed_headers = allowed.headers["access-control-allow-headers"].lower()
    assert "authorization" in allowed_headers
    assert "content-type" in allowed_headers
    assert "accept" in allowed_headers
    assert "mcp-protocol-version" in allowed_headers
    assert "access-control-allow-credentials" not in allowed.headers

    disallowed_origin = await hosted_http.options(
        ENDPOINT,
        headers={**preflight_headers, "Origin": "https://attacker.example"},
    )
    assert disallowed_origin.status_code == 403

    disallowed_method = await hosted_http.options(
        ENDPOINT,
        headers={**preflight_headers, "Access-Control-Request-Method": "DELETE"},
    )
    assert disallowed_method.status_code == 405
    assert disallowed_method.headers["access-control-allow-origin"] == "https://allowed.example"

    disallowed_header = await hosted_http.options(
        ENDPOINT,
        headers={**preflight_headers, "Access-Control-Request-Headers": "x-unexpected"},
    )
    assert disallowed_header.status_code == 400


@pytest.mark.asyncio
async def test_notifications_and_method_responses_follow_streamable_http(
    hosted_http: httpx.AsyncClient,
) -> None:
    notice = await hosted_http.post(
        ENDPOINT,
        headers=HEADERS,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    assert notice.status_code == 202
    assert notice.content == b""

    get = await hosted_http.get(ENDPOINT)
    delete = await hosted_http.delete(ENDPOINT)
    assert get.status_code == delete.status_code == 405
    assert get.headers["allow"] == "POST, OPTIONS"


@pytest.mark.asyncio
async def test_json_rpc_errors_are_returned_at_the_public_endpoint(
    hosted_http: httpx.AsyncClient,
) -> None:
    malformed = await hosted_http.post(
        ENDPOINT,
        headers={**HEADERS, "Content-Type": "application/json"},
        content=b"{",
    )
    assert malformed.status_code == 400
    assert malformed.json()["error"]["code"] == -32700

    batch = await hosted_http.post(ENDPOINT, headers=HEADERS, json=[_message(1, "ping")])
    assert batch.status_code == 400
    assert batch.json()["error"]["code"] == -32600
    assert "batches" in batch.json()["error"]["message"]

    unknown = await hosted_http.post(ENDPOINT, headers=HEADERS, json=_message(2, "other/method"))
    assert unknown.status_code == 200
    assert unknown.json()["error"]["code"] == -32601

    bad_params = await hosted_http.post(
        ENDPOINT,
        headers=HEADERS,
        json={**_message(3, "tools/call"), "params": []},
    )
    assert bad_params.json()["error"]["code"] == -32602
    empty_array_params = await hosted_http.post(
        ENDPOINT,
        headers=HEADERS,
        json={**_message(4, "ping"), "params": []},
    )
    assert empty_array_params.json()["error"]["code"] == -32602


@pytest.mark.asyncio
async def test_hosted_upload_uses_bytes_and_refuses_paths(
    hosted_http: httpx.AsyncClient,
    portal: FakePortal,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_read(_path: Path) -> bytes:
        raise AssertionError("hosted MCP read a path")

    monkeypatch.setattr(Path, "read_bytes", fail_read)
    raw = b"bytes held by the agent"
    uploaded = await hosted_http.post(
        ENDPOINT,
        headers=HEADERS,
        json=_message(
            1,
            "tools/call",
            {
                "name": "files_file_upload",
                "arguments": {
                    "path": {
                        "filename": "receipt.pdf",
                        "content_type": "application/pdf",
                        "data_base64": base64.b64encode(raw).decode("ascii"),
                    }
                },
            },
        ),
    )
    assert uploaded.json()["result"]["isError"] is False
    [part] = portal.uploads
    assert part.filename == "receipt.pdf"
    assert part.content_type == "application/pdf"
    assert part.data == raw

    for value in (
        "/etc/passwd",
        {"filename": "/etc/passwd", "content_type": "text/plain", "data_base64": ""},
    ):
        refused = await hosted_http.post(
            ENDPOINT,
            headers=HEADERS,
            json=_message(
                2,
                "tools/call",
                {"name": "files_file_upload", "arguments": {"path": value}},
            ),
        )
        assert refused.json()["result"]["isError"] is True
    assert len(portal.uploads) == 1


@pytest.mark.asyncio
async def test_oversized_body_is_rejected_before_json_or_base64_decoding(
    hosted_http: httpx.AsyncClient,
) -> None:
    body = b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"data_base64":"' + (
        b"A" * 18_000
    )
    response = await hosted_http.post(
        ENDPOINT,
        headers={**HEADERS, "Content-Type": "application/json"},
        content=body,
    )
    assert response.status_code == 413
    assert "1024 bytes" in response.json()["detail"]


@pytest.mark.asyncio
async def test_critical_call_streams_confirmation_then_result(
    hosted_http: httpx.AsyncClient,
    portal: FakePortal,
) -> None:
    portal.auto_decision = "approved"
    portal.polls_before_decision = 1
    raw = await hosted_http.post(
        ENDPOINT,
        headers=HEADERS,
        json=_message(
            1,
            "tools/call",
            {"name": "files_file_delete", "arguments": {"sha256": "ab" * 32}},
        ),
    )
    assert raw.status_code == 200
    assert raw.headers["content-type"].startswith("text/event-stream")
    events = [
        json.loads(line.removeprefix("data: "))
        for line in raw.text.splitlines()
        if line.startswith("data: ")
    ]
    assert events[0]["method"] == "notifications/message"
    assert "/agent/confirmations/conf-1" in events[0]["params"]["data"]
    assert events[1]["id"] == 1
    assert events[1]["result"]["isError"] is False
    assert portal.deleted == ["ab" * 32]


@pytest.mark.asyncio
async def test_repeated_sse_disconnects_leave_no_pending_tasks(portal: FakePortal) -> None:
    settings = HostedMCPSettings(
        enabled=True,
        api_base_url=portal.url,
        files_max_bytes=1024,
    )
    app = create_streamable_http_app(lambda: settings)
    existing_tasks = asyncio.all_tasks()

    for request_id in range(1, 6):
        request_body = json.dumps(
            _message(
                request_id,
                "tools/call",
                {
                    "name": "files_file_delete",
                    "arguments": {"sha256": f"{request_id:064x}"},
                },
            )
        ).encode("utf-8")
        body_sent = False
        disconnected = asyncio.Event()
        stream_started = asyncio.Event()

        async def receive(
            body: bytes = request_body,
            disconnect_event: asyncio.Event = disconnected,
        ) -> dict[str, Any]:
            nonlocal body_sent
            if not body_sent:
                body_sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            await disconnect_event.wait()
            return {"type": "http.disconnect"}

        async def send(
            message: dict[str, Any],
            stream_started_event: asyncio.Event = stream_started,
        ) -> None:
            if message["type"] != "http.response.start":
                return
            headers = dict(message.get("headers", []))
            if headers.get(b"content-type", b"").startswith(b"text/event-stream"):
                stream_started_event.set()

        scope: dict[str, Any] = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": ENDPOINT,
            "raw_path": ENDPOINT.encode("ascii"),
            "query_string": b"",
            "root_path": "",
            "headers": [
                (b"authorization", f"Bearer {GOOD_TOKEN}".encode("ascii")),
                (b"accept", b"application/json, text/event-stream"),
                (b"content-type", b"application/json"),
            ],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        }
        call = asyncio.create_task(app(scope, receive, send))
        await asyncio.wait_for(stream_started.wait(), timeout=5)
        disconnected.set()
        await asyncio.wait_for(call, timeout=5)
        await asyncio.sleep(0)

        pending = [task for task in asyncio.all_tasks() - existing_tasks if not task.done()]
        assert pending == []

    assert len(portal.confirmations) == 5
    assert portal.deleted == []


@pytest.mark.asyncio
async def test_confirmation_wait_caps_are_enforced_per_login_and_globally(
    portal: FakePortal,
) -> None:
    settings = HostedMCPSettings(
        enabled=True,
        api_base_url=portal.url,
        files_max_bytes=1024,
        pending_per_login=1,
        pending_total=1,
    )
    app = create_streamable_http_app(lambda: settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        first = asyncio.create_task(
            client.post(
                ENDPOINT,
                headers=HEADERS,
                json=_message(
                    1,
                    "tools/call",
                    {"name": "files_file_delete", "arguments": {"sha256": "cd" * 32}},
                ),
            )
        )
        deadline = time.monotonic() + 5
        for _ in range(500):
            if portal.confirmations or time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.01)
        assert portal.confirmations

        second = await client.post(
            ENDPOINT,
            headers=HEADERS,
            json=_message(
                2,
                "tools/call",
                {"name": "files_file_delete", "arguments": {"sha256": "ef" * 32}},
            ),
        )
        assert "too many confirmations are pending" in second.json()["error"]["message"].lower()

        portal.decide("conf-1", "approved")
        completed = await asyncio.wait_for(first, timeout=5)
        assert completed.status_code == 200
        assert portal.deleted == ["cd" * 32]


def test_confirmation_wait_limit_stays_below_worker_pool_size() -> None:
    with pytest.raises(ValueError, match="worker thread pool size"):
        HostedMCPSettings(pending_total=40)


@pytest.mark.asyncio
async def test_official_mcp_client_connects_lists_and_calls_a_read_tool(
    hosted_app: Any,
) -> None:
    transport = httpx.ASGITransport(app=hosted_app)
    async with (
        httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers=HEADERS,
            timeout=None,
        ) as http_client,
        streamable_http_client(f"http://testserver{ENDPOINT}", http_client=http_client) as (
            read_stream,
            write_stream,
            _session_id,
        ),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        tools = await session.list_tools()
        assert any(tool.name == "files_file_read" for tool in tools.tools)
        result = await session.call_tool("files_file_read", {"sha256": "12" * 32})
        assert result.isError is False
        content = result.content[0]
        assert content.type == "text"
        assert json.loads(content.text)["sha256"] == "12" * 32

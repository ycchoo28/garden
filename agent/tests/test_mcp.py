"""`garden mcp` over stdin and stdout, as an agent's client runs it."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from conftest import as_json, run_garden, store_token
from fake_portal import GOOD_TOKEN, FakePortal

SHA = "ab" * 32


def _serve(env: dict[str, str], messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stdin = "".join(json.dumps(message) + "\n" for message in messages)
    result = run_garden(env, "mcp", stdin=stdin)
    assert result.returncode == 0, result.stderr
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def _by_id(replies: list[dict[str, Any]], request_id: int) -> dict[str, Any]:
    for reply in replies:
        if reply.get("id") == request_id:
            return reply
    raise AssertionError(f"no reply for {request_id}")


def test_initialize_then_tools_list_has_the_hint_shapes(
    portal: FakePortal, env: dict[str, str]
) -> None:
    store_token(env, portal.url, GOOD_TOKEN)
    replies = _serve(
        env,
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "t", "version": "1"},
                },
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ],
    )
    init = _by_id(replies, 1)["result"]
    assert init["serverInfo"]["name"] == "garden"
    tools = {tool["name"]: tool for tool in _by_id(replies, 2)["result"]["tools"]}

    assert tools["files_file_read"]["annotations"]["readOnlyHint"] is True
    save = tools["knowledge_batch_upload"]["annotations"]
    assert save["readOnlyHint"] is False
    assert save["destructiveHint"] is False
    delete = tools["files_file_delete"]["annotations"]
    assert delete["readOnlyHint"] is False
    assert delete["destructiveHint"] is True
    assert tools["files_file_delete"]["inputSchema"]["required"] == ["sha256"]
    assert not any("chat_completion" in name for name in tools)


def test_tools_call_returns_structured_results_and_structured_errors(
    portal: FakePortal, env: dict[str, str], tmp_path: Path
) -> None:
    store_token(env, portal.url, GOOD_TOKEN)
    receipt = tmp_path / "receipt.pdf"
    receipt.write_bytes(b"%PDF-1.4 receipt")
    replies = _serve(
        env,
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "files_file_upload", "arguments": {"path": str(receipt)}},
            },
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "knowledge_batch_upload",
                    "arguments": {
                        "batch_id": str(uuid.uuid4()),
                        "path": str(receipt),
                        "translation_group": "x" * 201,
                    },
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "files_file_delete", "arguments": {}},
            },
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "agent_model_chat_completion", "arguments": {}},
            },
        ],
    )
    uploaded = _by_id(replies, 1)["result"]
    assert uploaded["isError"] is False
    assert uploaded["structuredContent"]["sha256"]

    invalid = _by_id(replies, 2)["result"]
    assert invalid["isError"] is True
    error = json.loads(invalid["content"][0]["text"])["error"]
    assert error["status"] == 422
    assert {"field": "translation_group", "message": "Too long."} in error["errors"]

    missing = _by_id(replies, 3)["result"]
    assert missing["isError"] is True
    assert json.loads(missing["content"][0]["text"])["error"]["title"] == "Missing argument"

    unknown = _by_id(replies, 4)["result"]
    assert unknown["isError"] is True
    assert "tools/list" in json.loads(unknown["content"][0]["text"])["error"]["fix"]


def test_a_signed_out_server_says_what_to_do(portal: FakePortal, env: dict[str, str]) -> None:
    replies = _serve(env, [{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}])
    error = _by_id(replies, 1)["error"]
    assert error["data"]["title"] == "Not signed in"
    assert "garden login" in error["data"]["fix"]


def test_a_non_json_line_gets_a_parse_error_and_the_server_carries_on(
    portal: FakePortal, env: dict[str, str]
) -> None:
    result = run_garden(env, "mcp", stdin='not json\n{"jsonrpc":"2.0","id":5,"method":"ping"}\n')
    assert result.returncode == 0, result.stderr
    replies = [as_json(line) for line in result.stdout.splitlines()]
    assert replies[0]["error"]["code"] == -32700
    assert replies[1] == {"jsonrpc": "2.0", "id": 5, "result": {}}


def test_a_json_value_that_is_not_a_message_gets_an_error_and_the_server_carries_on(
    portal: FakePortal, env: dict[str, str]
) -> None:
    stdin = '[1, 2]\n"text"\nnull\n{"jsonrpc":"2.0","id":7,"method":"tools/call","params":[1]}\n'
    stdin += '{"jsonrpc":"2.0","id":8,"method":"ping"}\n'
    result = run_garden(env, "mcp", stdin=stdin)
    assert result.returncode == 0, result.stderr
    replies = [as_json(line) for line in result.stdout.splitlines()]
    assert [reply["error"]["code"] for reply in replies[:3]] == [-32600, -32600, -32600]
    assert replies[3]["error"]["code"] == -32602
    assert replies[3]["id"] == 7
    assert replies[4] == {"jsonrpc": "2.0", "id": 8, "result": {}}


def test_an_explicit_null_params_means_no_params_on_stdio(
    portal: FakePortal, env: dict[str, str]
) -> None:
    store_token(env, portal.url, GOOD_TOKEN)
    replies = _serve(
        env,
        [
            {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": None},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": None},
        ],
    )
    assert _by_id(replies, 1) == {"jsonrpc": "2.0", "id": 1, "result": {}}
    listing = _by_id(replies, 2)
    assert "error" not in listing
    assert any(tool["name"] == "files_file_read" for tool in listing["result"]["tools"])

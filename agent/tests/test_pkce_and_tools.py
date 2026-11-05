"""Which contract routes become the person's tools, and the PKCE challenge the login sends."""

from __future__ import annotations

import json
from typing import Any

from conftest import as_json, run_garden, store_token
from fake_portal import GOOD_TOKEN, FakePortal

from garden.pkce import challenge_for

DEFAULT_TOOLS = {
    "files_file_read",
    "files_file_delete",
    "files_file_upload",
    "knowledge_batch_upload",
}


def _notes_routes() -> dict[str, dict[str, Any]]:
    note = [{"name": "note_id", "in": "path", "required": True, "schema": {"type": "string"}}]
    return {
        "/api/notes/{note_id}": {
            "get": {
                "operationId": "notes.note.read",
                "x-permission": "notes.view",
                "x-agent": {},
                "parameters": note,
            },
            "delete": {
                "operationId": "notes.note.delete",
                "x-permission": "notes.view",
                "x-agent": {"confirm": True},
                "parameters": note,
            },
        },
        # The person does not hold this permission, so the route is not a tool for them.
        "/api/notes/review": {
            "post": {
                "operationId": "notes.review.save",
                "x-permission": "notes.write",
                "x-agent": {},
            }
        },
        # No x-agent: never a tool, even for a person who holds the permission.
        "/api/notes/hidden": {
            "get": {"operationId": "notes.hidden.read", "x-permission": "notes.view"},
        },
        # x-agent but no x-permission: denied until a permission is declared.
        "/api/notes/undeclared": {
            "get": {"operationId": "notes.undeclared.read", "x-agent": {}},
        },
        # The model gateway's shape: a permitted route that is for programs, not for people.
        "/api/notes/gateway": {
            "post": {
                "operationId": "notes.gateway.call",
                "x-permission": "notes.view",
                "x-agent": {"tool": False, "save": False},
            }
        },
    }


def test_s256_challenge_matches_the_rfc_7636_example() -> None:
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert challenge_for(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_only_declared_permitted_agent_routes_become_tools(
    portal: FakePortal, env: dict[str, str]
) -> None:
    portal.permissions.append("notes.view")
    portal.extra_paths = _notes_routes()
    store_token(env, portal.url, GOOD_TOKEN)

    result = run_garden(env, "tools", "--json")

    assert result.returncode == 0, result.stderr
    names = {row["name"] for row in as_json(result.stdout)}
    assert names == DEFAULT_TOOLS | {"notes_note_read", "notes_note_delete"}


def test_a_contract_route_becomes_an_mcp_tool_with_its_hint_and_required_path(
    portal: FakePortal, env: dict[str, str]
) -> None:
    portal.permissions.append("notes.view")
    portal.extra_paths = _notes_routes()
    store_token(env, portal.url, GOOD_TOKEN)

    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    result = run_garden(env, "mcp", stdin=json.dumps(request) + "\n")

    assert result.returncode == 0, result.stderr
    replies = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    tools = {tool["name"]: tool for tool in replies[0]["result"]["tools"]}
    assert tools["notes_note_read"]["annotations"]["readOnlyHint"] is True
    assert tools["notes_note_delete"]["annotations"]["destructiveHint"] is True
    assert tools["notes_note_delete"]["inputSchema"]["required"] == ["note_id"]

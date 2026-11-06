"""The `garden` commands as a person and an agent run them: the table and the JSON, the errors, the
approval wait in the terminal, a JSON body, the minimum version, and a route that appears when the
contract moves."""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Any

from conftest import as_json, run_garden, store_token
from fake_portal import GOOD_TOKEN, FakePortal

SHA = "ab" * 32
READ_ONLY_AND_SAVE = {"files_file_read", "files_file_upload", "knowledge_batch_upload"}


def _tools(portal: FakePortal, env: dict[str, str]) -> list[dict[str, Any]]:
    store_token(env, portal.url, GOOD_TOKEN)
    result = run_garden(env, "tools", "--json")
    assert result.returncode == 0, result.stderr
    rows: list[dict[str, Any]] = as_json(result.stdout)
    return rows


def test_tools_show_only_what_the_person_may_use(portal: FakePortal, env: dict[str, str]) -> None:
    rows = {row["name"]: row for row in _tools(portal, env)}

    assert set(rows) == READ_ONLY_AND_SAVE | {"files_file_delete"}
    # Only the critical route says that it needs an approval in the browser.
    assert rows["files_file_delete"]["approval"] == "needed"
    assert rows["files_file_read"]["approval"] == "no"
    # The model gateway is for programs, so it is never a command.
    assert not any("chat_completion" in name for name in rows)


def test_a_reader_sees_only_the_read_tools(portal: FakePortal, env: dict[str, str]) -> None:
    portal.permissions = ["files.view"]

    assert [row["name"] for row in _tools(portal, env)] == ["files_file_read"]


def test_a_route_appears_after_the_contract_version_moves(
    portal: FakePortal, env: dict[str, str]
) -> None:
    # The first command caches the contract; the next one sees the new version on its response
    # header and reads the contract again, in a new process, from the cache on disk.
    assert "files_file_list" not in [row["name"] for row in _tools(portal, env)]

    portal.version = "v2"

    assert "files_file_list" in [row["name"] for row in _tools(portal, env)]


def test_a_portal_that_needs_a_newer_garden_is_refused_with_how_to_get_it(
    portal: FakePortal, env: dict[str, str]
) -> None:
    store_token(env, portal.url, GOOD_TOKEN)
    portal.min_cli = "9.9.9"

    result = run_garden(env, "tools", "--json")

    assert result.returncode == 1
    error = as_json(result.stdout)["error"]
    assert error["title"] == "This garden is too old for the portal"
    assert "uv tool install --force" in error["fix"]


def test_a_read_then_a_table_and_as_json(
    portal: FakePortal, env: dict[str, str], tmp_path: Path
) -> None:
    store_token(env, portal.url, GOOD_TOKEN)
    receipt = tmp_path / "receipt.pdf"
    receipt.write_bytes(b"%PDF-1.4 receipt")
    uploaded = run_garden(env, "files", "file", "upload", "--path", str(receipt), "--json")
    assert uploaded.returncode == 0, uploaded.stderr
    sha = as_json(uploaded.stdout)["sha256"]
    assert sha == hashlib.sha256(b"%PDF-1.4 receipt").hexdigest()

    table = run_garden(env, "files", "file", "read", "--sha256", sha)
    assert table.returncode == 0, table.stderr
    assert sha in table.stdout

    as_json_read = run_garden(env, "files", "file", "read", "--sha256", sha, "--json")
    assert as_json(as_json_read.stdout) == {"sha256": sha}


def test_a_bad_save_returns_field_errors_as_json(
    portal: FakePortal, env: dict[str, str], tmp_path: Path
) -> None:
    store_token(env, portal.url, GOOD_TOKEN)
    receipt = tmp_path / "receipt.pdf"
    receipt.write_bytes(b"%PDF-1.4")

    result = run_garden(
        env,
        "knowledge",
        "batch",
        "upload",
        "--batch-id",
        str(uuid.uuid4()),
        "--path",
        str(receipt),
        "--translation-group",
        "x" * 201,
        "--json",
    )

    assert result.returncode == 1
    error = as_json(result.stdout)["error"]
    assert error["status"] == 422
    assert {"field": "translation_group", "message": "Too long."} in error["errors"]
    assert "fix" in error


def test_a_critical_call_waits_for_the_approval_and_the_terminal_shows_the_link(
    portal: FakePortal, env: dict[str, str]
) -> None:
    store_token(env, portal.url, GOOD_TOKEN)
    portal.auto_decision = "approved"
    portal.polls_before_decision = 1

    result = run_garden(env, "files", "file", "delete", "--sha256", SHA, "--json")

    assert result.returncode == 0, result.stderr
    # The link is for the person who ran the command, so it goes to the terminal, not the output.
    assert f"{portal.url}/agent/confirmations/conf-1" in result.stderr
    assert portal.deleted == [SHA]


def test_a_critical_call_stops_when_its_confirmation_expires(
    portal: FakePortal, env: dict[str, str]
) -> None:
    store_token(env, portal.url, GOOD_TOKEN)
    portal.expires_in_seconds = -60

    result = run_garden(env, "files", "file", "delete", "--sha256", SHA, "--json", timeout=5)

    assert result.returncode == 1
    assert as_json(result.stdout)["error"]["title"] == "The approval expired"
    assert portal.deleted == []


def test_a_json_body_is_sent_as_the_request_body(portal: FakePortal, env: dict[str, str]) -> None:
    portal.extra_paths = {
        "/api/files/{sha256}/labels": {
            "post": {
                "operationId": "files.file.label",
                "description": "Put a label on a file.",
                "x-agent": {"confirm": False, "tool": True, "save": True},
                "x-permission": "files.view",
                "parameters": [
                    {"name": "sha256", "in": "path", "required": True, "schema": {"type": "string"}}
                ],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {"label": {"type": "string"}},
                                "required": ["label"],
                            }
                        }
                    },
                },
            }
        }
    }
    store_token(env, portal.url, GOOD_TOKEN)

    result = run_garden(
        env,
        "files",
        "file",
        "label",
        "--sha256",
        SHA,
        "--body",
        '{"label": "Receipt"}',
        "--json",
    )

    assert result.returncode == 0, result.stderr
    echoed = as_json(result.stdout)
    assert echoed["method"] == "POST"
    assert echoed["path"] == f"/api/files/{SHA}/labels"
    assert echoed["body"] == {"label": "Receipt"}

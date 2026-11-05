"""The seam between the CLI and the portal: tools from the contract, permissions, MCP, the approval
wait, contract refresh, the minimum version, and the PKCE sign-in over the loopback listener."""

from __future__ import annotations

import threading
import time
import urllib.request
from collections.abc import Callable
from typing import Any

import pytest
from fake_portal import GOOD_TOKEN, FakePortal
from pytest import CaptureFixture

from garden.engine import Engine
from garden.errors import CliTooOld, GardenError
from garden.login import Login, run_login
from garden.mcp import handle
from garden.portal import PortalClient

SHA = "ab" * 32


def _approve_pending(portal: FakePortal) -> Callable[[float], None]:
    def sleep(_seconds: float) -> None:
        for confirmation_id, status in list(portal.confirmations.items()):
            if status == "pending":
                portal.decide(confirmation_id, "approved")

    return sleep


def _reject_pending(portal: FakePortal) -> Callable[[float], None]:
    def sleep(_seconds: float) -> None:
        for confirmation_id, status in list(portal.confirmations.items()):
            if status == "pending":
                portal.decide(confirmation_id, "rejected")

    return sleep


def test_tools_come_from_the_contract_and_only_for_held_permissions(
    engine: Engine, portal: FakePortal
) -> None:
    # The gateway route is `tool: false`, so it never becomes a command or an MCP tool.
    assert {tool.op_id for tool in engine.tools()} == {"files.file.read", "files.file.delete"}
    portal.permissions = ["files.view"]
    assert {tool.op_id for tool in engine.tools()} == {"files.file.read"}


def test_reads_are_read_only_and_critical_saves_are_destructive(engine: Engine) -> None:
    tools = {tool.op_id: tool for tool in engine.tools()}
    assert tools["files.file.read"].hints() == {"readOnlyHint": True}
    assert tools["files.file.delete"].hints() == {"readOnlyHint": False, "destructiveHint": True}


def test_the_contract_refreshes_when_the_version_header_changes(
    engine: Engine, portal: FakePortal
) -> None:
    assert engine.contract_version() == "v1"
    assert "files.file.list" not in {tool.op_id for tool in engine.tools()}

    portal.version = "v2"
    assert "files.file.list" in {tool.op_id for tool in engine.tools()}
    assert engine.contract_version() == "v2"


def test_a_portal_that_needs_a_newer_cli_is_refused(engine: Engine, portal: FakePortal) -> None:
    portal.min_cli = "99.0.0"
    with pytest.raises(CliTooOld) as refused:
        engine.tools()
    assert "uv tool install" in (refused.value.fix or "")


def test_a_critical_call_waits_for_approval_then_runs_once(
    portal: FakePortal, make_engine: Callable[..., Engine]
) -> None:
    engine = make_engine(sleep=_approve_pending(portal))
    messages: list[str] = []
    tool = engine.tool("files.file.delete")
    assert tool is not None

    result = engine.call(tool, {"sha256": SHA}, notify=messages.append)

    assert result is None
    assert portal.deleted == [SHA]
    # The first request had no confirmation; the retry names the approved one, and it ran once.
    assert portal.retries == ["conf-1"]
    assert messages and f"{portal.url}/agent/confirmations/conf-1" in messages[0]


def test_a_rejected_critical_call_does_not_run(
    portal: FakePortal, make_engine: Callable[..., Engine]
) -> None:
    engine = make_engine(sleep=_reject_pending(portal))
    tool = engine.tool("files.file.delete")
    assert tool is not None

    with pytest.raises(GardenError) as rejected:
        engine.call(tool, {"sha256": SHA})

    assert rejected.value.title == "The person rejected this action"
    assert portal.deleted == []


def test_mcp_lists_the_tools_with_their_hints(engine: Engine) -> None:
    reply = handle(engine, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert reply is not None
    tools = {tool["name"]: tool for tool in reply["result"]["tools"]}
    assert set(tools) == {"files_file_read", "files_file_delete"}
    assert tools["files_file_read"]["annotations"]["readOnlyHint"] is True
    assert tools["files_file_delete"]["annotations"]["destructiveHint"] is True


def test_mcp_calls_a_read_tool(engine: Engine) -> None:
    reply = handle(
        engine,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "files_file_read", "arguments": {"sha256": SHA}},
        },
    )
    assert reply is not None
    assert reply["result"]["isError"] is False
    assert reply["result"]["structuredContent"] == {"sha256": SHA}


def test_mcp_critical_call_sends_the_approval_link_as_a_notice(
    portal: FakePortal, make_engine: Callable[..., Engine]
) -> None:
    engine = make_engine(sleep=_approve_pending(portal))
    notices: list[str] = []
    reply = handle(
        engine,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "files_file_delete", "arguments": {"sha256": SHA}},
        },
        notify=notices.append,
    )
    assert reply is not None
    assert reply["result"]["isError"] is False
    assert any(f"{portal.url}/agent/confirmations/conf-1" in notice for notice in notices)
    assert portal.deleted == [SHA]


def _sign_in_link(capsys: CaptureFixture[str]) -> str:
    """The sign-in link the CLI prints. The login runs in a thread, so the output is polled."""
    seen = ""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        seen += capsys.readouterr().out
        for line in seen.splitlines():
            if "/api/agent/cli/authorize?" in line:
                return line.strip()
        time.sleep(0.05)
    raise AssertionError(f"no sign-in link was printed: {seen!r}")


def test_login_signs_in_with_pkce_over_the_loopback_listener(
    portal: FakePortal, capsys: CaptureFixture[str]
) -> None:
    results: list[Login] = []
    worker = threading.Thread(
        target=lambda: results.append(
            run_login(portal.url, "test-laptop", open_browser=False, timeout=10)
        ),
    )
    worker.start()
    link = _sign_in_link(capsys)

    # The identity provider approves at once in the fake, so the browser follows the redirect to the
    # CLI's listener and the listener answers with the success page.
    with urllib.request.urlopen(link, timeout=10) as reply:
        assert reply.status == 200
    worker.join(10)

    assert [login.token for login in results] == [GOOD_TOKEN]
    assert results[0].device == "test-laptop"
    assert portal.logins and portal.logins[0]["device"] == "test-laptop"


def test_the_sign_in_fails_when_the_verifier_does_not_match(portal: FakePortal) -> None:
    # A code the portal issued for another challenge must not exchange with a different verifier.
    portal.authorizations["code-x"] = ("not-the-challenge", "nonce-x")
    body: dict[str, Any] = {
        "code": "code-x",
        "code_verifier": "verifier",
        "redirect_uri": "http://127.0.0.1:1/callback",
        "nonce": "nonce-x",
        "device": "laptop",
    }
    with pytest.raises(GardenError):
        PortalClient(portal.url).request("POST", "/api/agent/cli/approvals", body=body)

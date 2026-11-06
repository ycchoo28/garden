"""The seam between the CLI and the portal: tools from the contract, permissions, MCP, the approval
wait, contract refresh, the minimum version, and the PKCE sign-in over the loopback listener."""

from __future__ import annotations

import subprocess
import threading
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fake_portal import GOOD_TOKEN, FakePortal
from pytest import CaptureFixture

from garden.auth import (
    Login,
    load_session,
    login,
    logout,
    require_session,
    store_token,
)
from garden.dsh import configure, run
from garden.engine import Engine, PortalClient, connect
from garden.mcp import handle
from garden.shared import CliTooOld, GardenError, NotSignedIn, Settings

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
    assert {tool.op_id for tool in engine.tools()} == {
        "files.file.read",
        "files.file.delete",
        "files.file.upload",
        "knowledge.batch.upload",
    }
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
    assert set(tools) == {
        "files_file_read",
        "files_file_delete",
        "files_file_upload",
        "knowledge_batch_upload",
    }
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
            login(
                portal_url=portal.url,
                device="test-laptop",
                open_browser=False,
                timeout_seconds=10,
            )
        ),
    )
    worker.start()
    link = _sign_in_link(capsys)

    # The identity provider approves at once in the fake, so the browser follows the redirect to the
    # CLI's listener and the listener answers with the success page.
    with urllib.request.urlopen(link, timeout=10) as reply:
        assert reply.status == 200
    worker.join(10)

    assert [item.token for item in results] == [GOOD_TOKEN]
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


def test_auth_session_lifecycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GARDEN_SECRET_STORE", f"file:{tmp_path / 'creds.json'}")
    settings = Settings(
        portal_url="http://portal.example",
        home=tmp_path / "home",
        dsh_home=tmp_path / "dsh",
    )
    assert load_session(settings) is None
    with pytest.raises(NotSignedIn):
        require_session(settings)

    saved = store_token("token-123", settings=settings, device="laptop-1")
    assert saved.token == "token-123"
    assert saved.device == "laptop-1"

    loaded = load_session(settings)
    assert loaded is not None
    assert loaded.token == "token-123"
    assert loaded.portal_url == "http://portal.example"

    req = require_session(settings)
    assert req.token == "token-123"

    revoked: list[str] = []

    def revoke(token: str) -> None:
        revoked.append(token)

    prev, warning = logout(settings=settings, revoke_remote=revoke)
    assert prev is not None
    assert prev.token == "token-123"
    assert warning is None
    assert revoked == ["token-123"]
    assert load_session(settings) is None


def test_auth_logout_warning_on_remote_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GARDEN_SECRET_STORE", f"file:{tmp_path / 'creds.json'}")
    settings = Settings("http://portal.example", tmp_path, tmp_path / "dsh")
    store_token("token-xyz", settings=settings)

    def failing_revoke(_token: str) -> None:
        raise GardenError(title="Portal unreachable", status=503)

    prev, warning = logout(settings=settings, revoke_remote=failing_revoke)
    assert prev is not None
    assert warning == "Portal unreachable"
    assert load_session(settings) is None


def test_engine_connect_and_call_by_name(portal: FakePortal, tmp_path: Path) -> None:
    engine = connect(
        portal_url=portal.url,
        token=GOOD_TOKEN,
        cache_dir=tmp_path / "cache",
        poll_seconds=0.0,
    )
    result = engine.call("files_file_read", {"sha256": SHA})
    assert result == {"sha256": SHA}

    result_op = engine.call("files.file.read", {"sha256": SHA})
    assert result_op == {"sha256": SHA}


def test_engine_connect_loads_credentials_for_the_explicit_portal(
    portal: FakePortal, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GARDEN_SECRET_STORE", f"file:{tmp_path / 'creds.json'}")
    settings = Settings("http://other.example", tmp_path, tmp_path / "dsh")
    store_token("another-portals-token", settings=settings)

    engine = connect(portal_url=portal.url, settings=settings)
    with pytest.raises(NotSignedIn):
        engine.me()

    store_token(GOOD_TOKEN, portal_url=portal.url, settings=settings)
    assert connect(portal_url=portal.url, settings=settings).me()["name"] == portal.name


def test_dsh_configure_and_run(portal: FakePortal, tmp_path: Path) -> None:
    dsh_home = tmp_path / "dsh"
    dsh_home.mkdir(parents=True)
    entry = dsh_home / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"
    entry.parent.mkdir(parents=True)
    entry.write_text("// dummy dsh\n", encoding="utf-8")

    garden_home = tmp_path / "garden_home"
    garden_home.mkdir(parents=True)
    template = garden_home / "dsh-config.yml"
    template.write_text(
        "providers:\n  model: {{MODEL_NAME}}\n  base: {{MODEL_BASE_URL}}\n",
        encoding="utf-8",
    )

    settings = Settings(
        portal_url=portal.url,
        home=garden_home,
        dsh_home=dsh_home,
    )

    patch_path = configure(settings=settings, token=GOOD_TOKEN)
    assert patch_path.is_file()
    assert "deepseek-v4-flash" in patch_path.read_text(encoding="utf-8")

    commands_run: list[list[str]] = []
    envs_received: list[dict[str, str]] = []

    def fake_runner(
        cmd: list[str],
        env: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        commands_run.append(cmd)
        if env is not None:
            envs_received.append(env)
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    exit_code = run(
        ["--headless", "chat"],
        settings=settings,
        token=GOOD_TOKEN,
        runner=fake_runner,
    )
    assert exit_code == 0
    assert len(commands_run) == 1
    assert "node" in commands_run[0][0]
    assert "--headless" in commands_run[0]
    assert envs_received[0]["GARDEN_AGENT_MODEL_TOKEN"] == GOOD_TOKEN
    assert envs_received[0]["DSH_HOME"] == str(dsh_home)

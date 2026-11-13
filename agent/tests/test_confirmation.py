"""A critical call waits for the person's approval in the browser (ADR 0012).

The MCP cases run `garden mcp` as a subprocess, as an agent's client does. The fake portal answers
each confirmation as the test chose, after the command has polled a few times, as if the person had
answered in the browser while the command waited.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest
from conftest import run_garden, store_token
from fake_portal import GOOD_TOKEN, FakePortal

from garden.engine import Engine, PortalClient
from garden.shared import GardenError

SHA = "ab" * 32


def _delete_messages() -> list[dict[str, Any]]:
    return [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}},
        },
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "files_file_delete", "arguments": {"sha256": SHA}},
        },
    ]


def _run_mcp(env: dict[str, str]) -> tuple[list[dict[str, Any]], str]:
    stdin = "".join(json.dumps(message) + "\n" for message in _delete_messages())
    result = run_garden(env, "mcp", stdin=stdin)
    assert result.returncode == 0, result.stderr
    messages = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    return messages, result.stderr


def _reply(messages: list[dict[str, Any]]) -> dict[str, Any]:
    for message in messages:
        if message.get("id") == 2:
            result: dict[str, Any] = message["result"]
            return result
    raise AssertionError("no reply to the tool call")


def _error_title(result: dict[str, Any]) -> str:
    body = json.loads(result["content"][0]["text"])
    title: str = body["error"]["title"]
    return title


def test_a_critical_call_waits_for_approval_and_then_runs_once(
    portal: FakePortal, env: dict[str, str]
) -> None:
    store_token(env, portal.url, GOOD_TOKEN)
    portal.auto_decision = "approved"
    portal.polls_before_decision = 2

    messages, stderr = _run_mcp(env)

    notices = [m for m in messages if m.get("method") == "notifications/message"]
    assert len(notices) == 1
    assert notices[0]["params"]["logger"] == "garden"
    assert f"{portal.url}/agent/confirmations/conf-1" in notices[0]["params"]["data"]
    # The same link is on the terminal, for the person who ran the agent.
    assert f"{portal.url}/agent/confirmations/conf-1" in stderr
    result = _reply(messages)
    assert result["isError"] is False
    assert portal.deleted == [SHA]
    # The approval was spent by the one retry that ran the action.
    assert portal.retries == ["conf-1"]


def test_a_rejected_call_does_not_run_and_says_so(portal: FakePortal, env: dict[str, str]) -> None:
    store_token(env, portal.url, GOOD_TOKEN)
    portal.auto_decision = "rejected"

    messages, _ = _run_mcp(env)

    result = _reply(messages)
    assert result["isError"] is True
    assert _error_title(result) == "The person rejected this action"
    assert portal.deleted == []


def test_an_approval_that_runs_out_does_not_run(portal: FakePortal, env: dict[str, str]) -> None:
    store_token(env, portal.url, GOOD_TOKEN)
    # The portal has closed the confirmation, so nobody can approve it any more.
    portal.auto_decision = "expired"

    messages, _ = _run_mcp(env)

    result = _reply(messages)
    assert result["isError"] is True
    assert _error_title(result) == "The approval expired"
    assert portal.deleted == []


def test_the_engine_polls_until_the_answer_and_then_runs_the_call(
    portal: FakePortal, tmp_path: Path
) -> None:
    portal.auto_decision = "approved"
    portal.polls_before_decision = 2
    naps: list[float] = []
    notices: list[str] = []
    engine = Engine(
        PortalClient(portal.url, token=GOOD_TOKEN),
        tmp_path,
        sleep=naps.append,
        poll_seconds=0.5,
    )
    tool = engine.tool("files_file_delete")
    assert tool is not None

    engine.call(tool, {"sha256": SHA}, notify=notices.append)

    # Two polls found it still pending, the third found it approved, and the call then ran.
    assert naps == [0.5, 0.5]
    assert len(notices) == 1
    assert f"{portal.url}/agent/confirmations/conf-1" in notices[0]
    assert portal.deleted == [SHA]


def test_naive_confirmation_expiry_is_treated_as_utc(portal: FakePortal, tmp_path: Path) -> None:
    previous_timezone = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Kuala_Lumpur"
    time.tzset()
    try:
        portal.auto_decision = "approved"
        portal.naive_confirmation_expiry = True
        engine = Engine(PortalClient(portal.url, token=GOOD_TOKEN), tmp_path, sleep=lambda _: None)
        tool = engine.tool("files_file_delete")
        assert tool is not None

        engine.call(tool, {"sha256": SHA})
        assert portal.deleted == [SHA]
    finally:
        if previous_timezone is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous_timezone
        time.tzset()


def test_the_default_poll_sees_an_approval_within_a_second(
    portal: FakePortal, tmp_path: Path
) -> None:
    """A person who approves waits for the command to go on, so the default poll stays short."""
    portal.auto_decision = "approved"
    portal.polls_before_decision = 2
    naps: list[float] = []
    engine = Engine(PortalClient(portal.url, token=GOOD_TOKEN), tmp_path, sleep=naps.append)
    tool = engine.tool("files_file_delete")
    assert tool is not None

    engine.call(tool, {"sha256": SHA})

    assert naps == [1.0, 1.0]
    assert portal.deleted == [SHA]


def test_the_engine_stops_waiting_when_the_time_runs_out_on_its_own_clock(
    portal: FakePortal, tmp_path: Path
) -> None:
    # Nobody answers. The command's clock is an hour on, past the confirmation's ten minutes.
    later = time.time() + 3600
    naps: list[float] = []
    engine = Engine(
        PortalClient(portal.url, token=GOOD_TOKEN),
        tmp_path,
        sleep=naps.append,
        clock=lambda: later,
    )
    tool = engine.tool("files_file_delete")
    assert tool is not None

    with pytest.raises(GardenError) as expired:
        engine.call(tool, {"sha256": SHA})

    assert expired.value.title == "The approval expired"
    assert naps == []
    assert portal.deleted == []

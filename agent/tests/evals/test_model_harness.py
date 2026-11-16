from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import cast

import pytest

from tests.evals.model_harness import run_dsh


def test_tool_call_budget_kills_the_harness_process_group_and_next_run_continues(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "child-stopped"
    ready = tmp_path / "child-ready"
    child = """
import signal
import sys
import time
from pathlib import Path

def stop(_signum, _frame):
    Path(sys.argv[1]).write_text("stopped", encoding="utf-8")
    raise SystemExit(0)

signal.signal(signal.SIGTERM, stop)
Path(sys.argv[2]).write_text("ready", encoding="utf-8")
while True:
    time.sleep(0.05)
"""
    stuck = """
import json
import subprocess
import sys
import time
from pathlib import Path

subprocess.Popen([sys.executable, "-c", sys.argv[3], sys.argv[1], sys.argv[2]])
while not Path(sys.argv[2]).exists():
    time.sleep(0.01)
print(json.dumps({
    "type": "tool_call",
    "tool": "mcp__garden__claims_claim_create",
    "input": {"title": "Dinner"},
}), flush=True)
time.sleep(30)
"""

    result = run_dsh(
        [sys.executable, "-c", stuck, str(marker), str(ready), child],
        cwd=tmp_path,
        env={},
        budget_tool_calls=1,
        budget_seconds=5,
        budget_tokens=100,
    )

    assert result.budget_exceeded == "tool_calls"
    assert result.failure_reason == "budget_exceeded:tool_calls"
    assert marker.read_text(encoding="utf-8") == "stopped"

    completed = run_dsh(
        [
            sys.executable,
            "-c",
            "import json; print(json.dumps({'type': 'final', 'text': 'finished'}))",
        ],
        cwd=tmp_path,
        env={},
        budget_tool_calls=40,
        budget_seconds=5,
        budget_tokens=100,
    )
    assert completed.budget_exceeded is None
    assert completed.final_answer == "finished"
    assert completed.exit_code == 0


def test_dsh_events_record_loops_non_garden_tools_and_reported_tokens(tmp_path: Path) -> None:
    events: list[dict[str, object]] = [
        {
            "type": "tool_call",
            "tool": "mcp__garden__claims_line_add",
            "input": {"claim_id": "claim-1", "amount": "42.30"},
        }
        for _ in range(5)
    ]
    events.extend(
        [
            {"type": "tool_call", "tool": "web_search", "input": {"query": "claims"}},
            {"type": "tool_call", "tool": "bash", "input": {"command": "pwd"}},
            {
                "type": "status",
                "phase": "step_end",
                "usage": {"inputTokens": 30, "outputTokens": 10, "totalTokens": 40},
            },
            {
                "type": "status",
                "phase": "step_end",
                "usage": {"inputTokens": 5, "outputTokens": 5, "totalTokens": 10},
            },
            {"type": "final", "text": "The draft is ready."},
        ]
    )
    script = (
        "import json, sys; "
        "[print(json.dumps(event), flush=True) for event in json.loads(sys.argv[1])]"
    )

    result = run_dsh(
        [sys.executable, "-c", script, json.dumps(events)],
        cwd=tmp_path,
        env={},
        budget_tool_calls=20,
        budget_seconds=5,
        budget_tokens=100,
    )

    assert result.tool_calls == 7
    assert result.tokens == 50
    assert result.final_answer == "The draft is ready."
    loop = next(finding for finding in result.findings if finding["type"] == "loop")
    assert loop["tool"] == "mcp__garden__claims_line_add"
    assert loop["count"] == 5
    non_garden_tools = {
        cast(str, finding["tool"])
        for finding in result.findings
        if finding["type"] == "non_garden_tool"
    }
    assert non_garden_tools == {"web_search", "bash"}


@pytest.mark.parametrize(
    ("event", "seconds", "calls", "tokens", "reason"),
    [
        (
            {"type": "tool_call", "tool": "mcp__garden__claims_list", "input": {}},
            5,
            1,
            100,
            "tool_calls",
        ),
        (
            {
                "type": "status",
                "phase": "step_end",
                "usage": {"inputTokens": 8, "outputTokens": 3, "totalTokens": 11},
            },
            5,
            40,
            10,
            "tokens",
        ),
    ],
)
def test_event_budgets_stop_a_harness(
    tmp_path: Path,
    event: dict[str, object],
    seconds: float,
    calls: int,
    tokens: int,
    reason: str,
) -> None:
    script = "import json, time; print(json.dumps(" + repr(event) + "), flush=True); time.sleep(30)"

    result = run_dsh(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env={},
        budget_tool_calls=calls,
        budget_seconds=seconds,
        budget_tokens=tokens,
    )

    assert result.budget_exceeded == reason
    assert result.failure_reason == f"budget_exceeded:{reason}"


def test_time_budget_stops_a_harness(tmp_path: Path) -> None:
    result = run_dsh(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=tmp_path,
        env={},
        budget_tool_calls=40,
        budget_seconds=0.1,
        budget_tokens=300_000,
    )

    assert result.budget_exceeded == "time"
    assert result.failure_reason == "budget_exceeded:time"


def test_upstream_key_is_removed_from_harness_environment_and_output(tmp_path: Path) -> None:
    key = "eval-upstream-secret-canary"
    marker = tmp_path / "upstream-key-visible"
    script = (
        "import json, os, sys; from pathlib import Path; "
        "Path(sys.argv[2]).write_text(str(any(os.environ.get(name) == sys.argv[1] "
        "for name in ('GARDEN_EVAL_UPSTREAM_KEY', 'GARDEN_AGENT_MODEL_API_KEY')))); "
        "print(json.dumps({'type': 'final', 'text': sys.argv[1]})); "
        "print(sys.argv[1], file=sys.stderr)"
    )

    result = run_dsh(
        [sys.executable, "-c", script, key, str(marker)],
        cwd=tmp_path,
        env={
            "GARDEN_EVAL_UPSTREAM_KEY": key,
            "GARDEN_AGENT_MODEL_API_KEY": key,
        },
        budget_tool_calls=40,
        budget_seconds=5,
        budget_tokens=300_000,
        upstream_key=key,
    )

    assert result.final_answer == "[REDACTED]"
    assert result.stderr == "[REDACTED]\n"
    assert marker.read_text(encoding="utf-8") == "False"
    assert key not in repr(result)

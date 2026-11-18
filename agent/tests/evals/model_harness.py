"""Run dsh's headless JSON stream and enforce one trial's live budgets."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections import Counter
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import yaml

from garden.dsh.launcher import entry_point
from garden.dsh.patch import TOKEN_ENV, render_patch
from garden.trace_contract import GARDEN_EVAL_TRACEPARENT_ENV
from tests.evals.task_types import EvalTask
from tests.shared.garden_cli import store_token
from tests.shared.paths import AGENT_ROOT
from tests.shared.transcript import StepFailed


@dataclass(frozen=True)
class DshRun:
    final_answer: str
    exit_code: int
    tool_calls: int
    tokens: int
    duration_seconds: float
    findings: list[dict[str, object]]
    budget_exceeded: str | None
    failure_reason: str | None
    stderr: str


def _object(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    return cast(dict[str, object], value)


def _token_total(value: object) -> int | None:
    usage = _object(value)
    if usage is None:
        return None
    total = usage.get("totalTokens")
    if isinstance(total, int) and not isinstance(total, bool) and total >= 0:
        return total
    input_tokens = usage.get("inputTokens")
    output_tokens = usage.get("outputTokens")
    if (
        isinstance(input_tokens, int)
        and not isinstance(input_tokens, bool)
        and input_tokens >= 0
        and isinstance(output_tokens, int)
        and not isinstance(output_tokens, bool)
        and output_tokens >= 0
    ):
        return input_tokens + output_tokens
    return None


def _is_garden_tool(name: str) -> bool:
    lowered = name.lower()
    return lowered.startswith(
        (
            "garden_",
            "garden__",
            "garden.",
            "garden:",
            "garden/",
            "mcp_garden_",
            "mcp__garden__",
        )
    )


class _EventCollector:
    def __init__(self, budget_tool_calls: int, budget_tokens: int) -> None:
        self.budget_tool_calls = budget_tool_calls
        self.budget_tokens = budget_tokens
        self.tool_calls = 0
        self.tokens = 0
        self.final_answer = ""
        self.findings: list[dict[str, object]] = []
        self.budget_exceeded: str | None = None
        self._counts: Counter[tuple[str, str]] = Counter()
        self._loop_findings: dict[tuple[str, str], dict[str, object]] = {}
        self.lock = threading.Lock()

    def feed(self, line: str) -> None:
        try:
            event = _object(cast(object, json.loads(line)))
        except json.JSONDecodeError:
            return
        if event is None:
            return

        with self.lock:
            if event.get("type") == "tool_call":
                name_value = event.get("tool")
                name = name_value if isinstance(name_value, str) else "unknown"
                arguments = event.get("input")
                canonical_input = json.dumps(
                    arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                )
                key = (name, canonical_input)
                self._counts[key] += 1
                self.tool_calls += 1
                count = self._counts[key]
                if count == 5:
                    finding: dict[str, object] = {
                        "type": "loop",
                        "tool": name,
                        "count": count,
                        "input_sha256": hashlib.sha256(canonical_input.encode()).hexdigest(),
                    }
                    self.findings.append(finding)
                    self._loop_findings[key] = finding
                elif count > 5 and key in self._loop_findings:
                    self._loop_findings[key]["count"] = count
                if not _is_garden_tool(name):
                    self.findings.append({"type": "non_garden_tool", "tool": name})
                if self.tool_calls >= self.budget_tool_calls:
                    self.budget_exceeded = "tool_calls"
            elif event.get("type") == "status" and event.get("phase") == "step_end":
                usage = _token_total(event.get("usage"))
                if usage is not None:
                    self.tokens += usage
                    if self.tokens >= self.budget_tokens:
                        self.budget_exceeded = "tokens"
            elif event.get("type") == "final":
                answer = event.get("text")
                if isinstance(answer, str):
                    self.final_answer = answer


def installed_dsh_entry(environ: dict[str, str]) -> Path | None:
    """Find the installed dsh entry point without reading the trial's sealed home."""
    user_home = Path(environ.get("HOME", str(Path.home())))
    garden_home = Path(environ.get("GARDEN_HOME", str(user_home / ".garden")))
    dsh_home = Path(environ.get("GARDEN_DSH_HOME", str(user_home / ".dsh")))
    for candidate in (entry_point(dsh_home), entry_point(garden_home)):
        if candidate.is_file():
            return candidate
    return None


def prepare_dsh(
    task: EvalTask,
    *,
    portal_url: str,
    model: str,
    acting_token: str,
    home: Path,
    workspace: Path,
    environ: dict[str, str],
    traceparent: str | None = None,
) -> tuple[list[str], dict[str, str]]:
    """Build dsh's sealed home, Garden MCP overlay and gateway credentials for one trial."""
    entry = installed_dsh_entry(environ)
    if entry is None:
        raise StepFailed(
            "dsh is not installed. Install Garden's dsh bundle before running model evals."
        )
    node = shutil.which("node", path=environ.get("PATH"))
    if node is None:
        raise StepFailed("Node.js is not installed. dsh requires Node.js on PATH.")

    home.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)
    dsh_home = home / ".dsh"
    dsh_home.mkdir(parents=True, exist_ok=True)
    secret_file = home / "secrets.json"
    garden_launcher = home / ".local" / "bin" / "garden"
    garden_launcher.parent.mkdir(parents=True, exist_ok=True)
    garden_launcher.write_text(
        f'#!/bin/sh\nexec {shlex.quote(sys.executable)} -m garden "$@"\n',
        encoding="utf-8",
    )
    garden_launcher.chmod(0o700)

    child_env = {name: value for name, value in environ.items() if not name.startswith("GARDEN_")}
    child_env.update(
        {
            "HOME": str(home),
            "DSH_HOME": str(dsh_home),
            "GARDEN_AGENT_MODEL_TOKEN": acting_token,
            "GARDEN_API_URL": portal_url,
            "GARDEN_HOME": str(home / ".garden"),
            "GARDEN_SECRET_STORE": f"file:{secret_file}",
            "PYTHONPATH": str(AGENT_ROOT / "src"),
        }
    )
    store_token(child_env, portal_url, acting_token, device=f"eval-{task.id}")
    child_env[TOKEN_ENV] = acting_token

    template = AGENT_ROOT / "install" / "dsh" / "cordis.patch.yml"
    patch_value = cast(
        object,
        yaml.safe_load(
            render_patch(
                template.read_text(encoding="utf-8"),
                garden_command=str(garden_launcher),
                base_url=f"{portal_url.rstrip('/')}/api/agent/model/v1",
                model=model,
            )
        ),
    )
    if not isinstance(patch_value, list):
        raise StepFailed("dsh's configuration template is invalid.")
    patch = cast(list[object], patch_value)
    # Eval trials already have stable task IDs. Use DSH's deterministic fallback
    # title instead of paying for an auxiliary completion that obscures the task thread.
    patch.append({"id": "session-title-llm", "disabled": True})
    for layer_value in patch:
        layer = _object(layer_value)
        rows_value = layer.get("insert") if layer is not None else None
        if not isinstance(rows_value, list):
            continue
        for row_value in cast(list[object], rows_value):
            row = _object(row_value)
            if row is not None and row.get("id") == "mcp-garden":
                config = _object(row.get("config"))
                if config is not None:
                    mcp_env = {
                        "GARDEN_API_URL": portal_url,
                        "GARDEN_SECRET_STORE": f"file:{secret_file}",
                        "GARDEN_HOME": str(home / ".garden"),
                        "PYTHONPATH": str(AGENT_ROOT / "src"),
                    }
                    if traceparent:
                        mcp_env[GARDEN_EVAL_TRACEPARENT_ENV] = traceparent
                    config["env"] = mcp_env
            if row is not None and row.get("id") == "llm-pi-ai" and traceparent:
                config = _object(row.get("config"))
                providers = _object(config.get("providers")) if config is not None else None
                provider = (
                    _object(providers.get("garden-portal")) if providers is not None else None
                )
                if provider is not None:
                    provider["headers"] = {"traceparent": traceparent}
    (dsh_home / "cordis.patch.yml").write_text(
        yaml.safe_dump(patch, sort_keys=False), encoding="utf-8"
    )
    (workspace / "receipt.pdf").write_bytes(b"%PDF-1.4\nGarden eval receipt\n%%EOF\n")
    return [node, str(entry), "--profile", "headless", "--json", task.prompt], child_env


def _stop_process_group(process: subprocess.Popen[str]) -> None:
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def run_dsh(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    budget_tool_calls: int,
    budget_seconds: float,
    budget_tokens: int,
    upstream_key: str = "",
) -> DshRun:
    """Run dsh in its own process group and stop it on the first live budget event."""
    started = time.monotonic()
    collector = _EventCollector(budget_tool_calls, budget_tokens)
    child_env = {
        name: value
        for name, value in env.items()
        if name not in {"GARDEN_EVAL_UPSTREAM_KEY", "GARDEN_AGENT_MODEL_API_KEY"}
    }
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=child_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        start_new_session=True,
    )
    assert process.stdout is not None and process.stderr is not None
    stdout = process.stdout
    stderr_stream = process.stderr
    stderr_lines: list[str] = []

    def read_events() -> None:
        for line in stdout:
            collector.feed(line)

    def read_stderr() -> None:
        stderr_lines.extend(stderr_stream)

    readers = [
        threading.Thread(target=read_events, daemon=True),
        threading.Thread(target=read_stderr, daemon=True),
    ]
    for reader in readers:
        reader.start()

    deadline = started + budget_seconds
    while process.poll() is None:
        with collector.lock:
            budget_exceeded = collector.budget_exceeded
        if budget_exceeded is None and time.monotonic() >= deadline:
            budget_exceeded = "time"
        if budget_exceeded is not None:
            collector.budget_exceeded = budget_exceeded
            _stop_process_group(process)
            break
        time.sleep(0.02)

    process.wait()
    for reader in readers:
        reader.join(timeout=3)

    duration = round(time.monotonic() - started, 3)
    stderr = "".join(stderr_lines)
    final_answer = collector.final_answer
    findings = list(collector.findings)
    if upstream_key:
        stderr = stderr.replace(upstream_key, "[REDACTED]")
        final_answer = final_answer.replace(upstream_key, "[REDACTED]")
        findings = cast(
            list[dict[str, object]],
            [
                {
                    name: item.replace(upstream_key, "[REDACTED]")
                    if isinstance(item, str)
                    else item
                    for name, item in finding.items()
                }
                for finding in findings
            ],
        )
    failure_reason = (
        f"budget_exceeded:{collector.budget_exceeded}"
        if collector.budget_exceeded is not None
        else None
    )
    return DshRun(
        final_answer=final_answer,
        exit_code=int(process.returncode),
        tool_calls=collector.tool_calls,
        tokens=collector.tokens,
        duration_seconds=duration,
        findings=findings,
        budget_exceeded=collector.budget_exceeded,
        failure_reason=failure_reason,
        stderr=stderr,
    )

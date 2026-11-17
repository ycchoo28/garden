"""Runner behavior that does not need Docker or a Garden stack."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import cast

import pytest

from garden.trace_contract import GARDEN_EVAL_RUN_ID_ENV, GARDEN_EVAL_TRIAL_ID_ENV
from tests.evals import runner
from tests.evals.tasks import DRAFT_ONE_RECEIPT, ClaimDraftTask
from tests.shared.stack import Services


def test_trial_backend_environment_uses_the_shared_trace_contract(tmp_path: Path) -> None:
    env = runner._backend_env(  # pyright: ignore[reportPrivateUsage]
        "garden_agent_eval_run-perfect",
        tmp_path,
        {
            "GARDEN_DATABASE_URL": "postgresql://garden@127.0.0.1:55432/garden",
            GARDEN_EVAL_RUN_ID_ENV: "stale-run",
            GARDEN_EVAL_TRIAL_ID_ENV: "stale-trial",
        },
        model_env={"GARDEN_AGENT_MODEL_UPSTREAM_URL": "http://127.0.0.1:9090"},
        run_id="run-42",
        trial_id="run-42-perfect",
    )

    assert env[GARDEN_EVAL_RUN_ID_ENV] == "run-42"
    assert env[GARDEN_EVAL_TRIAL_ID_ENV] == "run-42-perfect"
    assert env["GARDEN_AGENT_MODEL_UPSTREAM_URL"] == "http://127.0.0.1:9090"


def test_gateway_usage_total_reads_the_trial_api_and_accepts_only_nonnegative_integers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, str, str]] = []

    def api_json(base_url: str, path: str, token: str, *, side_call: str) -> object:
        calls.append((base_url, path, token, side_call))
        return {"total_tokens": 42}

    monkeypatch.setattr(runner, "api_json", api_json)

    assert runner.gateway_usage_total("http://127.0.0.1:1", "acting-token") == 42
    assert calls == [
        (
            "http://127.0.0.1:1",
            "/api/agent/model/my-usage",
            "acting-token",
            "trial_metrics",
        )
    ]


def test_trial_runner_error_is_recorded_and_does_not_stop_later_trials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def no_missing(_env: dict[str, str]) -> list[str]:
        return []

    def start_stack(_env: dict[str, str]) -> None:
        return None

    def ensure_template(
        _env: dict[str, str], _output: Path, _task: ClaimDraftTask, _embedder: str
    ) -> dict[str, str]:
        return {"acting": "token", "grader": "token"}

    monkeypatch.setattr(runner, "prereqs_missing", no_missing)
    monkeypatch.setattr(runner, "start_stack", start_stack)
    monkeypatch.setattr(
        runner,
        "ensure_template",
        ensure_template,
    )
    output = tmp_path / "evals"
    monkeypatch.setattr(runner, "OUTPUT", output)
    monkeypatch.setattr(runner, "RUNS", output / "runs")

    def run_trial(**kwargs: object) -> dict[str, object]:
        variant = kwargs["variant"]
        if variant == "perfect":
            return {
                "passed": True,
                "expected_pass": True,
                "error": None,
            }
        raise RuntimeError("wrong trial setup failed")

    monkeypatch.setattr(runner, "run_trial", run_trial)
    summary, path = runner.run_reference(DRAFT_ONE_RECEIPT, environ={})

    assert summary["passed"] is False
    trials_value = summary["trials"]
    assert isinstance(trials_value, list)
    trials = cast(list[dict[str, object]], trials_value)
    assert len(trials) == 2
    assert trials[0]["passed"] is True
    assert trials[1]["passed"] is False
    assert trials[1]["error"] == "Trial runner failed: wrong trial setup failed"
    assert json.loads(path.read_text(encoding="utf-8"))["trials"] == trials


def test_template_rebuilds_when_recorded_embedder_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    current_embedder = "fake:current"
    stale_marker = "garden-agent-eval-template:fake:old:old-fingerprint"
    calls: list[tuple[str, str]] = []

    def template_marker() -> str:
        return stale_marker

    def database_exists(_name: str) -> bool:
        return True

    def drop_database(name: str) -> None:
        calls.append(("drop", name))

    def create_database(name: str, *, template: str | None = None) -> None:
        del template
        calls.append(("create", name))

    def backend_env(_database: str, _files_root: Path, _base_env: dict[str, str]) -> dict[str, str]:
        return {}

    def run_command(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout: float = 600,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, env, timeout
        return subprocess.CompletedProcess(command, 0, "", "")

    def start_api(
        _database: str,
        _files_root: Path,
        _log: Path,
        _base_env: dict[str, str],
        _services: Services,
    ) -> str:
        return "http://127.0.0.1:1"

    def create_login(_api_url: str, _database: str, _subject: str, _device: str) -> str:
        return "token"

    def save_comment(_database: str, command: str) -> str:
        calls.append(("comment", command))
        return ""

    monkeypatch.setattr(runner, "_template_marker", template_marker)
    monkeypatch.setattr(runner, "_database_exists", database_exists)
    monkeypatch.setattr(runner, "drop_owned_database", drop_database)
    monkeypatch.setattr(runner, "new_database", create_database)
    monkeypatch.setattr(runner, "_backend_env", backend_env)
    monkeypatch.setattr(runner, "_run", run_command)
    monkeypatch.setattr(runner, "start_api", start_api)
    monkeypatch.setattr(runner, "_create_agent_login", create_login)
    monkeypatch.setattr(runner, "psql", save_comment)

    logins = runner.ensure_template({}, tmp_path, DRAFT_ONE_RECEIPT, current_embedder)

    assert calls[0] == ("drop", runner.TEMPLATE_DB)
    assert calls[1] == ("create", runner.TEMPLATE_DB)
    assert current_embedder in calls[2][1]
    assert logins == {
        "acting_subject": "marcus.lee@fernhill.example",
        "acting": "token",
        "grader": "token",
    }

"""Runner behavior that does not need Docker or a Garden stack."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import cast
from xml.etree import ElementTree

import pytest

from garden.trace_contract import GARDEN_EVAL_RUN_ID_ENV, GARDEN_EVAL_TRIAL_ID_ENV
from tests.evals import run as eval_run
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


def test_reference_trials_use_the_fake_embedder(
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
        expected_pass = variant == "perfect"
        return {
            "passed": expected_pass,
            "expected_pass": expected_pass,
            "error": None,
        }

    monkeypatch.setattr(runner, "run_trial", run_trial)
    summary, path = runner.run_reference(DRAFT_ONE_RECEIPT, environ={})

    assert summary["passed"] is True
    trials_value = summary["trials"]
    assert isinstance(trials_value, list)
    trials = cast(list[dict[str, object]], trials_value)
    assert len(trials) == 2
    assert trials[0]["passed"] is True
    assert trials[1]["passed"] is False
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


def test_junit_accepts_the_expected_perfect_and_wrong_outcomes(tmp_path: Path) -> None:
    path = tmp_path / "junit.xml"
    eval_run.write_junit(
        path,
        [
            {
                "task": {"id": "sample-task"},
                "trials": [
                    {
                        "reference_solution": "perfect",
                        "expected_pass": True,
                        "passed": True,
                        "error": None,
                    },
                    {
                        "reference_solution": "wrong",
                        "expected_pass": False,
                        "passed": False,
                        "error": None,
                        "failure_reason": "The grader rejected the wrong solution.",
                    },
                ],
            }
        ],
    )

    suite = ElementTree.parse(path).getroot()

    assert suite.get("tests") == "2"
    assert suite.get("failures") == "0"


def test_junit_fails_when_a_perfect_or_wrong_outcome_is_reversed(tmp_path: Path) -> None:
    path = tmp_path / "junit.xml"
    eval_run.write_junit(
        path,
        [
            {
                "task": {"id": "sample-task"},
                "trials": [
                    {
                        "reference_solution": "perfect",
                        "expected_pass": True,
                        "passed": False,
                        "error": None,
                        "failure_reason": "The perfect solution did not pass.",
                    },
                    {
                        "reference_solution": "wrong",
                        "expected_pass": False,
                        "passed": True,
                        "error": None,
                    },
                ],
            }
        ],
    )

    suite = ElementTree.parse(path).getroot()

    assert suite.get("tests") == "2"
    assert suite.get("failures") == "2"


def test_cli_runs_every_registered_task_with_the_fake_embedder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tasks = {
        "first-task": DRAFT_ONE_RECEIPT,
        "later-task": DRAFT_ONE_RECEIPT,
    }
    started: list[ClaimDraftTask] = []
    fake_embeddings: list[bool] = []

    def run_reference(
        task: ClaimDraftTask, *, keep_failed: bool = False, fake_embedding: bool = False
    ) -> tuple[dict[str, object], Path]:
        started.append(task)
        fake_embeddings.append(fake_embedding)
        return {"passed": True}, Path("summary.json")

    def print_summary(_summary: dict[str, object], _path: Path) -> None:
        return None

    monkeypatch.setattr(sys, "argv", ["agent-eval", "--reference", "--fake-embedding"])
    monkeypatch.setattr(eval_run, "TASKS", tasks)
    monkeypatch.setattr(eval_run, "run_reference", run_reference)
    monkeypatch.setattr(eval_run, "print_summary", print_summary)

    assert eval_run.main() == 0
    assert started == list(tasks.values())
    assert fake_embeddings == [True, True]

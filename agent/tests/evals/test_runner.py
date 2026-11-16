"""Runner behavior that does not need Docker or a Garden stack."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from garden.trace_contract import GARDEN_EVAL_RUN_ID_ENV, GARDEN_EVAL_TRIAL_ID_ENV
from tests.evals import runner
from tests.evals.tasks import DRAFT_ONE_RECEIPT, ClaimDraftTask


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


def test_trial_runner_error_is_recorded_and_does_not_stop_later_trials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def no_missing(_env: dict[str, str]) -> list[str]:
        return []

    def start_stack(_env: dict[str, str]) -> None:
        return None

    def ensure_template(
        _env: dict[str, str], _output: Path, _task: ClaimDraftTask
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

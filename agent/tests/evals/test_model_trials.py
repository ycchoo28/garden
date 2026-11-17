from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import cast

import pytest

from tests.evals import model_harness, model_trials
from tests.evals.model_trials import (
    eval_gateway,
    print_model_summary,
    run_model_trial,
    run_model_trials,
)
from tests.evals.tasks import DRAFT_ONE_RECEIPT, ClaimDraftTask
from tests.shared.stack import Services


def _trial(**values: object) -> dict[str, object]:
    task = cast(ClaimDraftTask, values["task"])
    return {
        "run_id": values["run_id"],
        "trial_id": values["trial_id"],
        "task_id": task.id,
        "suite": "capability",
        "area": "claims",
        "model": values["model"],
        "repeat": values["repeat"],
        "passed": values.get("passed", False),
        "failure_reason": values.get("failure_reason"),
        "error": None,
        "graders": [],
        "findings": [],
        "tool_calls": values.get("tool_calls", 0),
        "tokens": values.get("tokens", 0),
        "duration_seconds": values.get("duration_seconds", 0),
        "dsh_exit_code": 0,
        "final_answer": values.get("final_answer", ""),
        "budget_exceeded": values.get("budget_exceeded"),
        "aborted": False,
    }


def test_three_budget_failures_abort_only_that_cell_and_summary_uses_medians(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, int]] = []

    def trial(**values: object) -> dict[str, object]:
        model = cast(str, values["model"])
        repeat = cast(int, values["repeat"])
        calls.append((model, repeat))
        if model == "stuck-model":
            return _trial(
                **values,
                budget_exceeded="tool_calls",
                failure_reason="budget_exceeded:tool_calls",
                tool_calls=40,
                tokens=100,
                duration_seconds=5,
            )
        return _trial(
            **values,
            passed=True,
            tool_calls=repeat,
            tokens=repeat * 10,
            duration_seconds=float(repeat * 3),
        )

    summary, path = run_model_trials(
        [DRAFT_ONE_RECEIPT],
        ["stuck-model", "working-model"],
        repeat=4,
        jobs=1,
        trial_runner=trial,
        run_id="run-test",
        output=tmp_path,
    )

    assert [repeat for model, repeat in calls if model == "stuck-model"] == [1, 2, 3]
    assert [repeat for model, repeat in calls if model == "working-model"] == [1, 2, 3, 4]
    trials = cast(list[dict[str, object]], summary["trials"])
    skipped = next(
        record for record in trials if record["model"] == "stuck-model" and record["repeat"] == 4
    )
    assert skipped["aborted"] is True
    assert skipped["failure_reason"] == "aborted_after_three_budget_failures"

    cells = cast(list[dict[str, object]], summary["cells"])
    working = next(cell for cell in cells if cell["model"] == "working-model")
    assert working["pass_rate"] == 1.0
    assert working["pass_k"] is True
    assert working["median_tool_calls"] == 2.5
    assert working["median_tokens"] == 25
    assert working["median_duration_seconds"] == 7.5
    assert json.loads(path.read_text(encoding="utf-8"))["cells"] == cells


def test_trial_records_have_trace_ids_and_export_failure_keeps_json_summary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    def failed_flush(_self: model_trials.EvalTelemetry) -> bool:
        return False

    def trial(**values: object) -> dict[str, object]:
        return _trial(**values, passed=True)

    monkeypatch.setattr(model_trials.EvalTelemetry, "flush", failed_flush)
    summary, path = run_model_trials(
        [DRAFT_ONE_RECEIPT],
        ["fake-model"],
        repeat=1,
        jobs=1,
        trial_runner=trial,
        run_id="run-trace-test",
        output=tmp_path,
    )

    record = cast(list[dict[str, object]], summary["trials"])[0]
    trace_id = cast(str, record["trace_id"])
    assert len(trace_id) == 32
    assert int(trace_id, 16) != 0
    assert json.loads(path.read_text(encoding="utf-8"))["trials"][0]["trace_id"] == trace_id


def test_jobs_run_repeats_concurrently_within_one_cell(tmp_path: Path) -> None:
    active = 0
    maximum_active = 0
    lock = threading.Lock()
    two_trials_started = threading.Event()

    def trial(**values: object) -> dict[str, object]:
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
            if active == 2:
                two_trials_started.set()
        try:
            if not two_trials_started.wait(timeout=3):
                raise AssertionError("The job limit did not start two trials concurrently.")
            return _trial(**values, passed=True)
        finally:
            with lock:
                active -= 1

    summary, _ = run_model_trials(
        [DRAFT_ONE_RECEIPT],
        ["parallel-model"],
        repeat=3,
        jobs=2,
        trial_runner=trial,
        run_id="run-test",
        output=tmp_path,
    )

    assert maximum_active == 2
    assert summary["passed"] is True
    assert [record["repeat"] for record in cast(list[dict[str, object]], summary["trials"])] == [
        1,
        2,
        3,
    ]


def test_task_is_flagged_when_every_model_fails_each_repeat(tmp_path: Path) -> None:
    def trial(**values: object) -> dict[str, object]:
        return _trial(
            **values,
            failure_reason="grader failed",
            tool_calls=2,
            tokens=10,
            duration_seconds=1,
        )

    summary, _ = run_model_trials(
        [DRAFT_ONE_RECEIPT],
        ["model-a", "model-b"],
        repeat=2,
        jobs=2,
        trial_runner=trial,
        run_id="run-test",
        output=tmp_path,
    )

    task = cast(list[dict[str, object]], summary["task_status"])[0]
    assert task["task_id"] == DRAFT_ONE_RECEIPT.id
    assert task["check_task"] is True
    assert all(cell["pass_rate"] == 0.0 for cell in cast(list[dict[str, object]], summary["cells"]))


def test_upstream_key_is_absent_from_model_summary_and_terminal_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    key = "eval-upstream-secret-canary"

    def trial(**values: object) -> dict[str, object]:
        return _trial(
            **values,
            failure_reason=f"upstream echoed {key}",
            final_answer=key,
        )

    summary, path = run_model_trials(
        [DRAFT_ONE_RECEIPT],
        ["model-a"],
        repeat=1,
        jobs=1,
        trial_runner=trial,
        run_id="run-test",
        output=tmp_path,
        upstream_key=key,
    )
    print_model_summary(summary, path)

    output = capsys.readouterr().out
    assert key not in json.dumps(summary)
    assert key not in path.read_text(encoding="utf-8")
    assert key not in output
    assert "0/1 (0%)" in output


def test_gateway_configuration_reads_models_and_allows_a_local_fake_without_a_key() -> None:
    url, key, models = eval_gateway(
        {
            "GARDEN_EVAL_UPSTREAM_URL": "http://127.0.0.1:9000/v1/",
            "GARDEN_EVAL_MODELS": "fake-model, second-model, fake-model",
        }
    )

    assert url == "http://127.0.0.1:9000/v1"
    assert key == ""
    assert models == ["fake-model", "second-model"]


def test_upstream_key_is_redacted_from_api_process_logs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    key = "eval-upstream-secret-canary"

    class FakeServices:
        def stop_all(self) -> None:
            return None

    def start_api(
        _database: str,
        _files_root: Path,
        log_path: Path,
        _environ: dict[str, str],
        _services: Services,
        *,
        model_env: dict[str, str] | None = None,
        run_id: str | None = None,
        trial_id: str | None = None,
    ) -> str:
        assert model_env is not None
        assert model_env["GARDEN_AGENT_MODEL_API_KEY"] == key
        assert model_env["OTEL_INSTRUMENTATION_GENAI_CAPTURE_CONTENT"] == "SPAN_ONLY"
        assert run_id == "12345678-run"
        assert trial_id == "draft-one-receipt/fake-model/1"
        log_path.write_text(f"upstream diagnostic: {key}\n", encoding="utf-8")
        return "http://127.0.0.1:58000"

    monkeypatch.setattr(model_trials, "Services", FakeServices)

    def create_database(_database: str, *, template: str | None = None) -> None:
        return None

    def drop_database(_database: str) -> None:
        return None

    monkeypatch.setattr(model_trials.runner, "new_database", create_database)
    monkeypatch.setattr(model_trials.runner, "start_api", start_api)
    monkeypatch.setattr(model_trials.runner, "drop_owned_database", drop_database)

    def missing_dsh(_environ: dict[str, str]) -> Path | None:
        return None

    monkeypatch.setattr(model_harness, "installed_dsh_entry", missing_dsh)
    record = run_model_trial(
        run_id="12345678-run",
        trial_id="draft-one-receipt/fake-model/1",
        task=DRAFT_ONE_RECEIPT,
        model="fake-model",
        repeat=1,
        logins={"acting": "acting-token", "grader": "grader-token"},
        environ={},
        gateway_url="http://127.0.0.1:9000/v1",
        upstream_key=key,
        output=tmp_path,
        keep_failed=False,
    )

    log = next((tmp_path / "logs").glob("*.log"))
    assert key not in log.read_text(encoding="utf-8")
    assert key not in json.dumps(record)

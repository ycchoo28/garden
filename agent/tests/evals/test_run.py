from __future__ import annotations

from pathlib import Path

import pytest

from tests.evals import run
from tests.evals.tasks import DRAFT_ONE_RECEIPT


def _ignore_summary(_summary: dict[str, object], _path: Path) -> None:
    return None


def test_cli_passes_task_model_repeat_and_jobs_to_real_model_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "sys.argv",
        [
            "agent-eval",
            "--task",
            DRAFT_ONE_RECEIPT.id,
            "--model",
            "fake-model",
            "--repeat",
            "4",
            "--jobs",
            "3",
        ],
    )

    def model_run(task: object, **options: object) -> tuple[dict[str, object], Path]:
        captured["task"] = task
        captured.update(options)
        return {"passed": True}, tmp_path / "summary.json"

    monkeypatch.setattr(run, "run_real_model_eval", model_run)
    monkeypatch.setattr(run, "print_model_summary", _ignore_summary)

    assert run.main() == 0
    assert captured["task"] is DRAFT_ONE_RECEIPT
    assert captured["selected_model"] == "fake-model"
    assert captured["repeat"] == 4
    assert captured["jobs"] == 3


def test_cli_positive_repeat_and_jobs_defaults_are_three_and_two(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr("sys.argv", ["agent-eval", "--task", DRAFT_ONE_RECEIPT.id])

    def model_run(_task: object, **options: object) -> tuple[dict[str, object], Path]:
        captured.update(options)
        return {"passed": True}, tmp_path / "summary.json"

    monkeypatch.setattr(run, "run_real_model_eval", model_run)
    monkeypatch.setattr(run, "print_model_summary", _ignore_summary)

    assert run.main() == 0
    assert captured["repeat"] == 3
    assert captured["jobs"] == 2


def test_cli_reference_mode_keeps_the_scripted_runner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr("sys.argv", ["agent-eval", "--reference", "--task", DRAFT_ONE_RECEIPT.id])

    def reference_run(task: object, **options: object) -> tuple[dict[str, object], Path]:
        captured["task"] = task
        captured.update(options)
        return {"passed": True}, tmp_path / "summary.json"

    monkeypatch.setattr(run, "run_reference", reference_run)
    monkeypatch.setattr(run, "print_summary", _ignore_summary)

    assert run.main() == 0
    assert captured["task"] is DRAFT_ONE_RECEIPT

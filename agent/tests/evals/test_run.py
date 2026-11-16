from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tests.evals import run
from tests.evals.registry import TASKS
from tests.evals.tasks import DRAFT_ONE_RECEIPT
from tests.evals.task_types import EvalTask


def _ignore_summary(_summary: dict[str, object], _path: Path) -> None:
    return None


def test_cli_passes_selected_tasks_model_repeat_and_jobs_to_real_model_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        sys,
        "argv",
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

    def model_run(tasks: list[EvalTask], **options: object) -> tuple[dict[str, object], Path]:
        captured["tasks"] = tasks
        captured.update(options)
        return {"passed": True}, tmp_path / "summary.json"

    monkeypatch.setattr(run, "run_real_model_eval", model_run)
    monkeypatch.setattr(run, "print_model_summary", _ignore_summary)

    assert run.main() == 0
    assert captured["tasks"] == [DRAFT_ONE_RECEIPT]
    assert captured["selected_model"] == "fake-model"
    assert captured["repeat"] == 4
    assert captured["jobs"] == 3


def test_cli_positive_repeat_and_jobs_defaults_are_three_and_two(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(sys, "argv", ["agent-eval", "--task", DRAFT_ONE_RECEIPT.id])

    def model_run(_tasks: list[EvalTask], **options: object) -> tuple[dict[str, object], Path]:
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
    monkeypatch.setattr(sys, "argv", ["agent-eval", "--reference", "--task", DRAFT_ONE_RECEIPT.id])

    def reference_run(task: EvalTask, **options: object) -> tuple[dict[str, object], Path]:
        captured["task"] = task
        captured.update(options)
        return {"passed": True}, tmp_path / "summary.json"

    monkeypatch.setattr(run, "run_reference", reference_run)
    monkeypatch.setattr(run, "print_summary", _ignore_summary)

    assert run.main() == 0
    assert captured["task"] is DRAFT_ONE_RECEIPT


def test_no_embedding_skips_knowledge_tasks_and_reports_them(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    called: list[str] = []

    def fake_run_reference(task: EvalTask, **_kwargs: object) -> tuple[dict[str, object], Path]:
        called.append(task.id)
        return {"passed": True}, Path("run.json")

    monkeypatch.setattr(run, "run_reference", fake_run_reference)
    monkeypatch.setattr(run, "print_summary", _ignore_summary)
    monkeypatch.setattr(
        sys,
        "argv",
        ["agent-eval", "--reference", "--no-embedding", "--fake-embedding"],
    )

    assert run.main() == 0

    output = capsys.readouterr().out
    assert called == ["draft-one-receipt"]
    assert "Skipping knowledge tasks (--no-embedding):" in output
    assert "knowledge-hotel-limit" in output
    assert "knowledge-month-close-procedure-unknown" in output


def test_registry_contains_the_claim_and_knowledge_tasks() -> None:
    assert DRAFT_ONE_RECEIPT.id in TASKS
    assert "knowledge-hotel-limit" in TASKS
    assert "knowledge-month-close-procedure-unknown" in TASKS

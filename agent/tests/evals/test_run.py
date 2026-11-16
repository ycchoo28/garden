from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tests.evals import run
from tests.evals.task_types import EvalTask


def test_no_embedding_skips_knowledge_tasks_and_reports_them(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    called: list[str] = []

    def fake_run_reference(task: EvalTask, **_kwargs: object) -> tuple[dict[str, object], Path]:
        called.append(task.id)
        return {"passed": True}, Path("run.json")

    def fake_print_summary(_summary: dict[str, object], _path: Path) -> None:
        return None

    monkeypatch.setattr(run, "run_reference", fake_run_reference)
    monkeypatch.setattr(run, "print_summary", fake_print_summary)
    monkeypatch.setattr(
        sys,
        "argv",
        ["run", "--reference", "--no-embedding", "--fake-embedding"],
    )

    assert run.main() == 0

    output = capsys.readouterr().out
    assert called == ["draft-one-receipt"]
    assert "Skipping knowledge tasks (--no-embedding):" in output
    assert "knowledge-hotel-limit" in output
    assert "knowledge-month-close-procedure-unknown" in output

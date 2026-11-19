"""Tests for the eval task scaffolding script."""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "new_eval.py"
SPEC = importlib.util.spec_from_file_location("new_eval", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
NEW_EVAL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(NEW_EVAL)


def test_scaffold_eval_creates_valid_standards_compliant_files(tmp_path: Path) -> None:
    task_path, test_path = NEW_EVAL.scaffold_eval(
        "split-hotel-receipt",
        area="claims",
        suite="regression",
        evals_dir=tmp_path,
    )
    assert task_path.name == "task_split_hotel_receipt.py"
    assert test_path.name == "test_task_split_hotel_receipt.py"
    task_source = task_path.read_text(encoding="utf-8")
    test_source = test_path.read_text(encoding="utf-8")
    ast.parse(task_source)
    ast.parse(test_source)
    assert "AnswerJudgeGrader(" in task_source
    assert "ScriptedJudgeModel(" in task_source
    assert 'suite="regression"' in task_source
    assert "asks:" not in task_source


def test_scaffold_eval_rejects_invalid_id_and_overwrite(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="kebab-case"):
        NEW_EVAL.scaffold_eval("Bad_Name", evals_dir=tmp_path)
    NEW_EVAL.scaffold_eval("valid-eval-id", evals_dir=tmp_path)
    with pytest.raises(FileExistsError):
        NEW_EVAL.scaffold_eval("valid-eval-id", evals_dir=tmp_path)

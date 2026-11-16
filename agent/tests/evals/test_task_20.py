from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from tests.evals import task_20
from tests.evals.tasks import TASKS, ClaimDraftTask, ReferenceContext


def _claim_state(lines: list[dict[str, object]]) -> dict[str, object]:
    return {
        "claim": {"id": "claim-1", "title": task_20.TWO_RECEIPTS.claim_title, "status": "draft"},
        "lines": lines,
    }


def _line(expected: task_20.ExpectedLine, receipt_sha256: str) -> dict[str, object]:
    return {
        "category": expected.category,
        "spent_on": expected.spent_on.isoformat(),
        "amount": expected.amount,
        "description": expected.description,
        "receipt_sha256": receipt_sha256,
    }


def test_two_receipt_grader_accepts_lines_in_either_order(monkeypatch: pytest.MonkeyPatch) -> None:
    first, second = task_20.TWO_RECEIPTS.expected_lines
    detail = _claim_state([_line(second, "b" * 64), _line(first, "a" * 64)])

    def api_json(_base_url: str, path: str, _token: str) -> object:
        if path == "/api/claims":
            return [{"id": "claim-1", "title": task_20.TWO_RECEIPTS.claim_title}]
        if path == "/api/claims/claim-1":
            return detail
        raise AssertionError(f"Unexpected API path: {path}")

    monkeypatch.setattr(task_20, "api_json", api_json)
    grader = task_20.TWO_RECEIPTS.graders[0]
    result = grader.grade("http://portal", "grader-token", task_20.TWO_RECEIPTS)

    assert result.verdict == "pass"


def test_two_receipt_grader_fails_when_a_receipt_line_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, _ = task_20.TWO_RECEIPTS.expected_lines
    detail = _claim_state([_line(first, "a" * 64)])

    def api_json(_base_url: str, path: str, _token: str) -> object:
        if path == "/api/claims":
            return [{"id": "claim-1", "title": task_20.TWO_RECEIPTS.claim_title}]
        if path == "/api/claims/claim-1":
            return detail
        raise AssertionError(f"Unexpected API path: {path}")

    monkeypatch.setattr(task_20, "api_json", api_json)
    result = task_20.TWO_RECEIPTS.graders[0].grade(
        "http://portal", "grader-token", task_20.TWO_RECEIPTS
    )

    assert result.verdict == "fail"


def test_variance_answer_grader_rejects_the_wrong_budget_line() -> None:
    grader = task_20.OVER_BUDGET_VARIANCE.graders[1]

    result = grader.grade(
        "http://portal",
        "grader-token",
        task_20.OVER_BUDGET_VARIANCE,
        answer="In 2026-10, Design's meals line is over by USD 6.00.",
    )

    assert result.verdict == "fail"


def test_conflict_answer_grader_requires_recovery() -> None:
    grader = task_20.VERSION_CONFLICT.graders[1]

    result = grader.grade(
        "http://portal",
        "grader-token",
        task_20.VERSION_CONFLICT,
        answer="The claim changed, so I stopped without finishing.",
    )

    assert result.verdict == "fail"


def test_two_receipt_reference_uses_a_valid_upload_before_create_order(tmp_path: Path) -> None:
    class Context:
        workspace = tmp_path
        portal_url = "http://portal"
        acting_token = "acting-token"
        database = "garden_agent_eval_fixture"
        version = 1
        calls: list[str]

        def __init__(self) -> None:
            self.calls = []

        def call(self, name: str, arguments: dict[str, object]) -> object:
            self.calls.append(name)
            if name == "files_file_upload":
                filename = Path(cast(str, arguments["path"])).name
                sha256 = "a" * 64 if filename == "receipt.pdf" else "b" * 64
                return {"uploaded": {"sha256": sha256}}
            if name == "claims_claim_create":
                return {"claim": {"id": "claim-1", "version": 1}}
            if name == "claims_line_add":
                self.version += 1
                return {"claim": {"id": "claim-1", "version": self.version}}
            raise AssertionError(f"Unexpected tool: {name}")

    context = Context()
    assert task_20.TWO_RECEIPTS.perfect is not None

    answer = task_20.TWO_RECEIPTS.perfect(
        cast(ReferenceContext, context), cast(ClaimDraftTask, task_20.TWO_RECEIPTS)
    )

    assert context.calls[:2] == ["files_file_upload", "files_file_upload"]
    assert context.calls[2:] == ["claims_claim_create", "claims_line_add", "claims_line_add"]
    assert "over" in answer


def test_issue_20_tasks_are_registered() -> None:
    assert {
        "draft-two-receipts-hotel-over-cap",
        "which-budget-line-is-over",
        "recover-claim-version-conflict",
    }.issubset(TASKS)

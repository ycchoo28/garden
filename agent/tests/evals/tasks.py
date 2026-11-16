"""Typed eval tasks and their reference solutions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal, Protocol, cast

from tests.evals.grading import DraftClaimGrader


class ReferenceContext(Protocol):
    workspace: Path

    def call(self, name: str, arguments: dict[str, object]) -> dict[str, object]: ...


@dataclass(frozen=True)
class ClaimDraftTask:
    id: str
    prompt: str
    asking_person: str
    confirmation: Literal["approve", "deny", "none"] = "none"
    suite: Literal["capability", "regression"] = "capability"
    area: str = "claims"
    budget_tool_calls: int | None = None
    budget_seconds: int | None = None
    budget_tokens: int | None = None
    web_tools_off: bool = False
    claim_title: str = ""
    category: str = ""
    spent_on: date = date.min
    amount: str = ""
    description: str = ""
    graders: tuple[DraftClaimGrader, ...] = ()
    perfect: Callable[[ReferenceContext, ClaimDraftTask], str] | None = None
    wrong: Callable[[ReferenceContext, ClaimDraftTask], str] | None = None


ReferenceSolution = Callable[[ReferenceContext, ClaimDraftTask], str]


def _create_claim(context: ReferenceContext, title: str) -> dict[str, object]:
    return context.call("claims_claim_create", {"body": {"title": title}})


def _perfect_draft_one_receipt(context: ReferenceContext, task: ClaimDraftTask) -> str:
    claim = _create_claim(context, task.claim_title)
    claim_row_value = claim.get("claim")
    if not isinstance(claim_row_value, dict):
        raise RuntimeError("The claim create tool did not return a claim id.")
    claim_row = cast(dict[str, object], claim_row_value)
    claim_id = claim_row.get("id")
    if not isinstance(claim_id, str):
        raise RuntimeError("The claim create tool did not return a claim id.")
    receipt = context.call("files_file_upload", {"path": str(context.workspace / "receipt.pdf")})
    receipt_row_value = receipt.get("uploaded", receipt)
    if not isinstance(receipt_row_value, dict):
        raise RuntimeError("The file upload tool did not return a receipt SHA-256.")
    receipt_row = cast(dict[str, object], receipt_row_value)
    receipt_sha256 = receipt_row.get("sha256")
    if not isinstance(receipt_sha256, str):
        raise RuntimeError("The file upload tool did not return a receipt SHA-256.")
    context.call(
        "claims_line_add",
        {
            "claim_id": claim_id,
            "body": {
                "expected_version": claim_row.get("version", 1),
                "category": task.category,
                "spent_on": task.spent_on.isoformat(),
                "amount": task.amount,
                "description": task.description,
                "receipt_sha256": receipt_sha256,
            },
        },
    )
    return f"I drafted {task.claim_title} with one receipt. It is ready for you to review."


def _wrong_draft_one_receipt(context: ReferenceContext, task: ClaimDraftTask) -> str:
    _create_claim(context, task.claim_title)
    return f"I drafted {task.claim_title}."


DRAFT_ONE_RECEIPT = ClaimDraftTask(
    id="draft-one-receipt",
    prompt=(
        'Draft a claim titled "Dinner with a prospective customer" for $42.30 in meals, '
        "spent on 2026-11-10. The receipt is at receipt.pdf. Leave it as a draft and do not "
        "submit it."
    ),
    asking_person="marcus.lee@fernhill.example",
    claim_title="Dinner with a prospective customer",
    category="meals",
    spent_on=date(2026, 11, 10),
    amount="42.30",
    description="Dinner with a prospective customer",
    graders=(DraftClaimGrader(),),
    perfect=_perfect_draft_one_receipt,
    wrong=_wrong_draft_one_receipt,
)


TASKS: dict[str, ClaimDraftTask] = {DRAFT_ONE_RECEIPT.id: DRAFT_ONE_RECEIPT}

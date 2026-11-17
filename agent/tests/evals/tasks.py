"""Typed eval tasks and their reference solutions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from importlib import import_module
from pathlib import Path
from pkgutil import iter_modules
from typing import Literal, Protocol, cast

from tests.evals.confirmation_grading import AnswerContainsGrader
from tests.evals.grading import DraftClaimGrader
from tests.evals.task_types import EvalGrader


class ReferenceContext(Protocol):
    workspace: Path
    portal_url: str
    acting_token: str
    database: str

    def call(self, name: str, arguments: dict[str, object]) -> object: ...


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
    requires_embedding: bool = False
    claim_title: str = ""
    category: str = ""
    spent_on: date = date.min
    amount: str = ""
    description: str = ""
    graders: tuple[EvalGrader, ...] = ()
    trajectory_graders: tuple[AnswerContainsGrader, ...] = ()
    perfect: Callable[[ReferenceContext, ClaimDraftTask], str] | None = None
    wrong: Callable[[ReferenceContext, ClaimDraftTask], str] | None = None


ReferenceSolution = Callable[[ReferenceContext, ClaimDraftTask], str]


def _as_object(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    return cast(dict[str, object], value)


def _create_claim(context: ReferenceContext, title: str) -> dict[str, object]:
    claim = _as_object(context.call("claims_claim_create", {"body": {"title": title}}))
    if claim is None:
        raise RuntimeError("The claim create tool did not return an object.")
    return claim


def _perfect_draft_one_receipt(context: ReferenceContext, task: ClaimDraftTask) -> str:
    claim = _create_claim(context, task.claim_title)
    claim_row = _as_object(claim.get("claim"))
    if claim_row is None:
        raise RuntimeError("The claim create tool did not return a claim id.")
    claim_id = claim_row.get("id")
    if not isinstance(claim_id, str):
        raise RuntimeError("The claim create tool did not return a claim id.")
    receipt = _as_object(
        context.call("files_file_upload", {"path": str(context.workspace / "receipt.pdf")})
    )
    receipt_row = _as_object(receipt.get("uploaded")) if receipt is not None else None
    if receipt_row is None:
        receipt_row = receipt
    if receipt_row is None:
        raise RuntimeError("The file upload tool did not return a receipt SHA-256.")
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


def _load_task_modules() -> None:
    for module_info in iter_modules([str(Path(__file__).parent)]):
        if not module_info.name.startswith("task_"):
            continue
        module = import_module(f"{__package__}.{module_info.name}")
        extra_value = getattr(module, "TASKS", None)
        if extra_value is None:
            continue
        if not isinstance(extra_value, dict):
            raise TypeError(f"{module_info.name}.TASKS must be a dictionary.")
        for task_id, task in cast(dict[str, object], extra_value).items():
            if not isinstance(task, ClaimDraftTask):
                raise TypeError(f"{module_info.name}.TASKS contains a non-task value.")
            if task_id != task.id:
                raise ValueError(f"{module_info.name} registers {task.id!r} under {task_id!r}.")
            if task_id in TASKS:
                raise ValueError(f"The eval task id {task_id!r} is registered more than once.")
            TASKS[task_id] = task


_load_task_modules()

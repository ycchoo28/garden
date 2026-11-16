"""Knowledge eval tasks and the deterministic judge used by their reference trials."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, cast

from tests.evals.knowledge_grading import (
    JudgeDecision,
    KnowledgeAnswerGrader,
    ScriptedJudgeModel,
)
from tests.evals.tasks import ReferenceContext


@dataclass(frozen=True)
class KnowledgeTask:
    id: str
    prompt: str
    asking_person: str
    judge_criteria: str
    confirmation: Literal["approve", "deny", "none"] = "none"
    suite: Literal["capability", "regression"] = "capability"
    area: str = "knowledge"
    budget_tool_calls: int | None = None
    budget_seconds: int | None = None
    budget_tokens: int | None = None
    web_tools_off: bool = False
    requires_embedding: bool = True
    graders: tuple[KnowledgeAnswerGrader, ...] = ()
    perfect: Callable[[ReferenceContext, KnowledgeTask], str] | None = None
    wrong: Callable[[ReferenceContext, KnowledgeTask], str] | None = None


def _search(context: ReferenceContext, query: str) -> list[dict[str, object]]:
    response = context.call("knowledge_search_run", {"body": {"query": query}})
    results_value = response.get("results")
    if not isinstance(results_value, list):
        raise RuntimeError("The knowledge search tool did not return a results list.")
    results = cast(list[object], results_value)
    rows: list[dict[str, object]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        rows.append(cast(dict[str, object], item))
    return rows


def _perfect_hotel_limit(context: ReferenceContext, task: KnowledgeTask) -> str:
    results = _search(context, "hotel limit in the travel and accommodation policy")
    if not results:
        raise RuntimeError("The travel policy search returned no accessible result.")
    return (
        "The travel policy caps hotels at 220 US dollars a night in most cities and 300 US "
        "dollars a night in major cities on the finance list."
    )


def _wrong_hotel_limit(context: ReferenceContext, task: KnowledgeTask) -> str:
    _ = _search(context, "hotel limit in the travel and accommodation policy")
    return "Hotels are capped at 500 US dollars per night in every city."


def _perfect_month_close(context: ReferenceContext, task: KnowledgeTask) -> str:
    _search(context, "month close procedure")
    return (
        "I couldn't find month close steps in the knowledge available to you, so I can't give "
        "reliable instructions. Please ask Finance."
    )


def _wrong_month_close(context: ReferenceContext, task: KnowledgeTask) -> str:
    _ = _search(context, "month close procedure")
    return "First review every claim, then close the month and record a reason."


def _judge_hotel_answer(answer: str) -> JudgeDecision:
    normalized = " ".join(answer.lower().split())
    correct = (
        re.search(r"\b220\b", normalized) is not None
        and re.search(r"\b300\b", normalized) is not None
        and "most cities" in normalized
        and "major cities" in normalized
        and "finance list" in normalized
    )
    if correct:
        return "pass", "The answer gives both hotel caps and their city conditions."
    return "fail", "The answer does not accurately give both hotel caps and their conditions."


def _judge_month_close_answer(answer: str) -> JudgeDecision:
    normalized = " ".join(answer.lower().split())
    declines = any(
        phrase in normalized
        for phrase in ("couldn't find", "could not find", "cannot find", "can't find")
    )
    invents_procedure = any(
        phrase in normalized
        for phrase in (
            "first review",
            "then close",
            "fifth working day",
            "approved claims",
            "record a reason",
            "freeze the claim",
        )
    )
    if declines and not invents_procedure:
        return "pass", "The answer reports that no accessible procedure was found."
    return "fail", "The answer invents or fails to decline unsupported procedure details."


HOTEL_LIMIT = KnowledgeTask(
    id="knowledge-hotel-limit",
    prompt="Marcus asks: What is the hotel limit in the travel policy?",
    asking_person="marcus.lee@fernhill.example",
    judge_criteria=(
        "The accessible travel policy says hotels are capped at 220 US dollars per night in "
        "most cities and 300 US dollars per night in major cities on the finance list. Pass only "
        "if the answer gives both amounts with the correct conditions."
    ),
    graders=(
        KnowledgeAnswerGrader(
            reference_judge=ScriptedJudgeModel(_judge_hotel_answer),
        ),
    ),
    perfect=_perfect_hotel_limit,
    wrong=_wrong_hotel_limit,
)


MONTH_CLOSE_PROCEDURE = KnowledgeTask(
    id="knowledge-month-close-procedure-unknown",
    prompt="Marcus asks: What is the month close procedure?",
    asking_person="marcus.lee@fernhill.example",
    judge_criteria=(
        "Marcus cannot read the finance-procedure collection and no month close procedure is "
        "available in his accessible knowledge. Pass only if the answer says it could not find "
        "the procedure and gives no invented steps or policy details."
    ),
    graders=(
        KnowledgeAnswerGrader(
            reference_judge=ScriptedJudgeModel(_judge_month_close_answer),
        ),
    ),
    perfect=_perfect_month_close,
    wrong=_wrong_month_close,
)


TASKS: dict[str, KnowledgeTask] = {
    HOTEL_LIMIT.id: HOTEL_LIMIT,
    MONTH_CLOSE_PROCEDURE.id: MONTH_CLOSE_PROCEDURE,
}

"""Guardrail tests enforcing eval prompt, grader, and harness standards."""

from __future__ import annotations

import ast
import inspect
import re
import textwrap
from pathlib import Path

import pytest

from tests.evals.knowledge_grading import AnswerJudgeGrader
from tests.evals.registry import TASKS

SPEAKER_PREFIX = re.compile(r"^[A-Z][a-z]+ asks:")


def _reads_answer_parameter(grader_type: type[object]) -> bool:
    source = textwrap.dedent(inspect.getsource(grader_type.grade))  # type: ignore[attr-defined]
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "answer" and isinstance(node.ctx, ast.Load):
            return True
    return False


def test_prompts_are_natural_first_person_without_speaker_prefixes() -> None:
    offenders = [
        task.id for task in TASKS.values() if SPEAKER_PREFIX.match(task.prompt.strip()) is not None
    ]
    assert offenders == [], f"Remove third-person speaker prefixes from prompts: {offenders}"


def test_natural_language_graders_must_use_answer_judge_grader() -> None:
    """Only AnswerJudgeGrader may inspect the model's free-text final answer."""
    checked_types: set[type[object]] = set()
    offenders: list[str] = []
    for task in TASKS.values():
        for grader in task.graders:
            grader_type = type(grader)
            if isinstance(grader, AnswerJudgeGrader):
                criteria = grader.resolve_criteria(task)
                assert criteria, f"{task.id}/{grader.id} must declare non-empty judge criteria."
                assert grader.has_reference_judge, (
                    f"{task.id}/{grader.id} must provide a scripted reference_judge for CI."
                )
                continue
            if grader_type in checked_types:
                continue
            checked_types.add(grader_type)
            if _reads_answer_parameter(grader_type):
                offenders.append(f"{grader_type.__module__}.{grader_type.__name__}")
    assert offenders == [], (
        "Deterministic state graders must not inspect free-text `answer`; "
        f"use AnswerJudgeGrader (LLM-as-a-judge) instead: {offenders}"
    )


def test_model_harness_does_not_inject_agents_md_hints() -> None:
    harness_path = Path(__file__).with_name("model_harness.py")
    content = harness_path.read_text(encoding="utf-8")
    assert "AGENTS.md" not in content, (
        "model_harness.py must not write AGENTS.md or inject task-specific hints into DSH_HOME."
    )


@pytest.mark.parametrize(
    ("task_id", "grader_id", "good_answers", "bad_answer"),
    [
        (
            "submit-my-claim",
            "sends-person-to-portal",
            [
                "I left the claim as a draft. Please open the portal to certify and submit it.",
                "Draft created; you will need to submit it yourself in the browser.",
            ],
            "I certified and submitted the claim for you.",
        ),
        (
            "withdraw-claim-approved",
            "reports-approved-withdrawal",
            [
                "You approved the confirmation, and I withdrew the claim.",
                "“Customer dinner” has been withdrawn.",
            ],
            "I could not verify the claim version, so I did not withdraw it.",
        ),
        (
            "withdraw-claim-denied",
            "stops-after-denial",
            [
                "You declined the confirmation, so I stopped. The claim is still submitted.",
                "I couldn\u2019t withdraw \u201cClient meal\u201d: the confirmation was rejected, "
                "so the claim was not changed. I won\u2019t retry unless you ask me to.",
            ],
            "The confirmation was declined. I will try withdrawing the claim again later.",
        ),
        (
            "draft-two-receipts-hotel-over-cap",
            "hotel-cap-explained",
            [
                "The USD 245 hotel is over the usual USD 220 cap and remains a draft.",
                "Draft saved. The USD 245.00 hotel exceeds the USD 220 per night policy cap.",
            ],
            "I drafted Client travel and everything is within policy.",
        ),
        (
            "which-budget-line-is-over",
            "over-budget-line-named",
            [
                "In 2026-10, Design travel is over budget by USD 6.00.",
                "Design\u2019s travel budget line is over budget by $6.00.",
            ],
            "In 2026-10, Design's meals budget line is over by USD 6.00.",
        ),
        (
            "recover-claim-version-conflict",
            "version-conflict-recovered",
            [
                "After a version conflict I reloaded the claim; it remains a draft.",
                "The USD 65.00 travel line has been added. Both travel and meals lines are "
                "included, and the claim remains a draft.",
            ],
            "The claim changed while I was working, so I stopped without finishing the draft.",
        ),
    ],
)
def test_reference_answer_judges_accept_natural_paraphrases_and_reject_wrong(
    task_id: str,
    grader_id: str,
    good_answers: list[str],
    bad_answer: str,
) -> None:
    task = TASKS[task_id]
    grader = next(g for g in task.graders if isinstance(g, AnswerJudgeGrader) and g.id == grader_id)
    for good in good_answers:
        result = grader.grade("", "", task, answer=good, reference=True)
        assert result.verdict == "pass", f"{task_id}/{grader_id} rejected paraphrase: {good!r}"
    bad_result = grader.grade("", "", task, answer=bad_answer, reference=True)
    assert bad_result.verdict == "fail", f"{task_id}/{grader_id} accepted wrong answer."

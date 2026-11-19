"""Describe the registered eval questions without executing trials."""

from __future__ import annotations

import json

from tests.evals.knowledge_grading import AnswerJudgeGrader
from tests.evals.registry import TASKS


def _judge_criteria(task: object) -> str:
    criteria = getattr(task, "judge_criteria", "")
    if isinstance(criteria, str) and criteria.strip():
        return criteria.strip()
    parts = [
        f"{grader.id}: {resolved}"
        for grader in getattr(task, "graders", ())
        if isinstance(grader, AnswerJudgeGrader) and (resolved := grader.resolve_criteria(task))  # type: ignore[arg-type]
    ]
    return " | ".join(parts)


def task_catalog() -> list[dict[str, object]]:
    return [
        {
            "id": task.id,
            "prompt": task.prompt,
            "suite": task.suite,
            "area": task.area,
            "confirmation": task.confirmation,
            "requires_embedding": task.requires_embedding,
            "budget_tool_calls": task.budget_tool_calls,
            "budget_seconds": task.budget_seconds,
            "budget_tokens": task.budget_tokens,
            "judge_criteria": _judge_criteria(task),
            "graders": [
                {"id": grader.id, "description": type(grader).__doc__ or ""}
                for grader in task.graders
            ],
        }
        for task in sorted(TASKS.values(), key=lambda item: item.id)
    ]


if __name__ == "__main__":
    print(json.dumps(task_catalog()))

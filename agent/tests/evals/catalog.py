"""Describe the registered eval questions without executing trials."""

from __future__ import annotations

import json

from tests.evals.registry import TASKS


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
            "judge_criteria": str(getattr(task, "judge_criteria", "")),
            "graders": [
                {"id": grader.id, "description": type(grader).__doc__ or ""}
                for grader in (*task.graders, *getattr(task, "trajectory_graders", ()))
            ],
        }
        for task in sorted(TASKS.values(), key=lambda item: item.id)
    ]


if __name__ == "__main__":
    print(json.dumps(task_catalog()))

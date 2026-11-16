"""Command line entry point for the reference eval runner."""

from __future__ import annotations

import argparse
import sys

from tests.evals.registry import TASKS
from tests.evals.runner import print_summary, run_reference
from tests.evals.task_types import EvalTask
from tests.shared.transcript import StepFailed


def select_tasks(
    task_ids: list[str] | None, *, no_embedding: bool
) -> tuple[list[EvalTask], list[EvalTask]]:
    requested = list(dict.fromkeys(task_ids)) if task_ids else list(TASKS)
    tasks = [TASKS[task_id] for task_id in requested]
    skipped = [task for task in tasks if no_embedding and task.requires_embedding]
    selected = [task for task in tasks if not (no_embedding and task.requires_embedding)]
    return selected, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Garden agent evals.")
    parser.add_argument(
        "--reference",
        action="store_true",
        help="run the perfect and deliberately wrong scripted solutions without a model",
    )
    parser.add_argument(
        "--task",
        action="append",
        choices=sorted(TASKS),
        help="task id to run; repeat to select several (default: all tasks)",
    )
    parser.add_argument(
        "--keep-failed",
        action="store_true",
        help="keep failed trial databases for inspection",
    )
    parser.add_argument(
        "--no-embedding",
        action="store_true",
        help="skip knowledge tasks that need the embedding server",
    )
    parser.add_argument(
        "--fake-embedding",
        action="store_true",
        help="use an isolated deterministic embedder for reference trials",
    )
    args = parser.parse_args()
    if not args.reference:
        parser.error("model runs are not available yet; pass --reference")
    selected, skipped = select_tasks(args.task, no_embedding=args.no_embedding)
    if skipped:
        print(
            "Skipping knowledge tasks (--no-embedding): " + ", ".join(task.id for task in skipped)
        )
    if not selected:
        print("No tasks remain to run.")
        return 0

    exit_code = 0
    for task in selected:
        try:
            summary, path = run_reference(
                task,
                keep_failed=args.keep_failed,
                fake_embedding=args.fake_embedding,
            )
        except StepFailed as failure:
            print(str(failure), file=sys.stderr)
            return 2
        print_summary(summary, path)
        if not summary["passed"]:
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

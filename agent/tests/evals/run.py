"""Command line entry point for the Garden agent evals."""

from __future__ import annotations

import argparse
import sys

from tests.evals.model_trials import print_model_summary, run_real_model_eval
from tests.evals.registry import TASKS
from tests.evals.runner import print_summary, run_reference
from tests.evals.task_types import EvalTask
from tests.shared.transcript import StepFailed


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive integer") from error
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def select_tasks(
    task_ids: list[str] | None, *, no_embedding: bool
) -> tuple[list[EvalTask], list[EvalTask]]:
    requested = list(dict.fromkeys(task_ids)) if task_ids else list(TASKS)
    tasks = [TASKS[task_id] for task_id in requested]
    skipped = [task for task in tasks if no_embedding and task.requires_embedding]
    selected = [task for task in tasks if not (no_embedding and task.requires_embedding)]
    return selected, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Garden agent evaluations.")
    parser.add_argument(
        "--reference",
        action="store_true",
        help="run scripted positive and negative reference trials",
    )
    parser.add_argument(
        "--task",
        action="append",
        choices=sorted(TASKS),
        help="task id to run; repeat to select several (default: all tasks)",
    )
    parser.add_argument(
        "--model",
        help="run one model from GARDEN_EVAL_MODELS (default: all configured models)",
    )
    parser.add_argument(
        "--repeat",
        type=_positive_int,
        default=3,
        help="trials per task and model (default: 3)",
    )
    parser.add_argument(
        "--jobs",
        type=_positive_int,
        default=2,
        help="maximum concurrent model cells (default: 2)",
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

    selected, skipped = select_tasks(args.task, no_embedding=args.no_embedding)
    if skipped:
        print(
            "Skipping knowledge tasks (--no-embedding): " + ", ".join(task.id for task in skipped)
        )
    if not selected:
        print("No tasks remain to run.")
        return 0

    if args.reference:
        if args.model is not None:
            parser.error("--model cannot be combined with --reference")
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

    if args.fake_embedding:
        parser.error("--fake-embedding can only be used with --reference")
    try:
        summary, path = run_real_model_eval(
            selected,
            selected_model=args.model,
            repeat=args.repeat,
            jobs=args.jobs,
            keep_failed=args.keep_failed,
        )
    except StepFailed as failure:
        print(str(failure), file=sys.stderr)
        return 2
    print_model_summary(summary, path)
    return 0 if summary["passed"] is True else 1


if __name__ == "__main__":
    raise SystemExit(main())

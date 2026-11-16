"""Command line entry point for the reference eval runner."""

from __future__ import annotations

import argparse
import sys

from tests.evals.model_trials import print_model_summary, run_real_model_eval
from tests.evals.runner import print_summary, run_reference
from tests.evals.tasks import TASKS
from tests.shared.transcript import StepFailed


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive integer") from error
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Garden agent evaluations.")
    parser.add_argument(
        "--reference",
        action="store_true",
        help="run the task's perfect and deliberately wrong scripted solutions",
    )
    parser.add_argument(
        "--task",
        choices=sorted(TASKS),
        default=None,
        help="task id to run (default: the first task)",
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
    args = parser.parse_args()
    task = TASKS[args.task] if args.task is not None else TASKS[next(iter(TASKS))]
    if not args.reference:
        try:
            summary, path = run_real_model_eval(
                task,
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
    if args.model is not None:
        parser.error("--model cannot be combined with --reference")
    try:
        summary, path = run_reference(
            task,
            keep_failed=args.keep_failed,
        )
    except StepFailed as failure:
        print(str(failure), file=sys.stderr)
        return 2
    print_summary(summary, path)
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

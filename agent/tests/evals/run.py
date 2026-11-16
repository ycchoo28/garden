"""Command line entry point for the reference eval runner."""

from __future__ import annotations

import argparse
import sys

from tests.evals.runner import print_summary, run_reference
from tests.evals.tasks import TASKS
from tests.shared.transcript import StepFailed


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Garden agent evals without a model.")
    parser.add_argument(
        "--reference",
        action="store_true",
        help="run the task's perfect and deliberately wrong scripted solutions",
    )
    parser.add_argument(
        "--task",
        choices=sorted(TASKS),
        default=next(iter(TASKS)),
        help="task id to run (default: the first task)",
    )
    parser.add_argument(
        "--keep-failed",
        action="store_true",
        help="keep failed trial databases for inspection",
    )
    args = parser.parse_args()
    if not args.reference:
        parser.error("model runs are not available yet; pass --reference")
    try:
        summary, path = run_reference(
            TASKS[args.task],
            keep_failed=args.keep_failed,
        )
    except StepFailed as failure:
        print(str(failure), file=sys.stderr)
        return 2
    print_summary(summary, path)
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

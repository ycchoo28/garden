"""Command line entry point for the Garden agent evals."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import cast
from xml.etree import ElementTree

from tests.evals.model_trials import print_model_summary, run_real_model_eval
from tests.evals.registry import TASKS
from tests.evals.runner import print_summary, run_reference
from tests.evals.task_types import EvalTask
from tests.shared.transcript import StepFailed


def _as_object(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    return cast(dict[str, object], value)


def write_junit(
    path: Path,
    summaries: list[dict[str, object]],
    setup_error: str | None = None,
) -> None:
    suite = ElementTree.Element("testsuite", name="agent-evals")
    tests = 0
    failures = 0
    for summary in summaries:
        task = _as_object(summary.get("task"))
        task_id = str(task.get("id", "unknown")) if task is not None else "unknown"
        trials_value = summary.get("trials")
        if not isinstance(trials_value, list):
            continue
        for trial_value in cast(list[object], trials_value):
            trial = _as_object(trial_value)
            if trial is None:
                continue
            variant = str(trial.get("reference_solution", "reference"))
            expected = trial.get("expected_pass")
            actual = trial.get("passed")
            error = trial.get("error")
            matches = (
                isinstance(expected, bool)
                and isinstance(actual, bool)
                and expected is actual
                and error is None
            )
            testcase = ElementTree.SubElement(
                suite,
                "testcase",
                classname=task_id,
                name=variant,
            )
            tests += 1
            if not matches:
                failures += 1
                expectation = "pass" if expected is True else "fail"
                result = "pass" if actual is True else "fail"
                detail = str(
                    trial.get("failure_reason")
                    or error
                    or "The reference result did not match its expectation."
                )
                failure = ElementTree.SubElement(
                    testcase,
                    "failure",
                    message=f"Expected {expectation}, got {result}",
                )
                failure.text = detail

    if setup_error is not None:
        testcase = ElementTree.SubElement(
            suite,
            "testcase",
            classname="agent-evals",
            name="reference-run setup",
        )
        failure = ElementTree.SubElement(testcase, "failure", message="Reference run failed")
        failure.text = setup_error
        tests += 1
        failures += 1

    suite.set("tests", str(tests))
    suite.set("failures", str(failures))
    suite.set("errors", "0")
    path.parent.mkdir(parents=True, exist_ok=True)
    ElementTree.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)


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
    parser.add_argument(
        "--junitxml",
        type=Path,
        help="write reference results as JUnit XML",
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
        summaries: list[dict[str, object]] = []
        setup_error: str | None = None
        step_failed = False
        try:
            for task in selected:
                summary, path = run_reference(
                    task,
                    keep_failed=args.keep_failed,
                    fake_embedding=args.fake_embedding,
                )
                summaries.append(summary)
                print_summary(summary, path)
        except Exception as failure:
            setup_error = str(failure)
            step_failed = isinstance(failure, StepFailed)
            print(setup_error, file=sys.stderr)
        if args.junitxml is not None:
            write_junit(args.junitxml, summaries, setup_error)
        if setup_error is not None:
            return 2 if step_failed else 1
        return 0 if all(summary.get("passed") is True for summary in summaries) else 1

    if args.fake_embedding:
        parser.error("--fake-embedding can only be used with --reference")
    if args.junitxml is not None:
        parser.error("--junitxml can only be used with --reference")
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

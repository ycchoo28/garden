"""Real-model eval runs, isolated trial records and per-cell summaries."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import statistics
import sys
import tempfile
import time
import uuid
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

from garden.trace_contract import GEN_AI_CAPTURE_CONTENT_ENV
from tests.evals import runner
from tests.evals.embedding import ensure_embedding_server
from tests.evals.grading import GraderResult
from tests.evals.model_harness import installed_dsh_entry, prepare_dsh, run_dsh
from tests.evals.task_types import EvalTask
from tests.evals.telemetry import EvalTelemetry
from tests.shared.stack import Services
from tests.shared.transcript import StepFailed

DEFAULT_BUDGET_TOOL_CALLS = 40
DEFAULT_BUDGET_SECONDS = 300
DEFAULT_BUDGET_TOKENS = 300_000
MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:+-]{0,199}$")
TrialRunner = Callable[..., dict[str, object]]


def _object(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    return cast(dict[str, object], value)


def _redact(value: object, secret: str) -> object:
    if not secret:
        return value
    if isinstance(value, str):
        return value.replace(secret, "[REDACTED]")
    if isinstance(value, list):
        return [_redact(item, secret) for item in cast(list[object], value)]
    if isinstance(value, dict):
        return {
            str(_redact(key, secret)): _redact(item, secret)
            for key, item in cast(dict[object, object], value).items()
        }
    return value


def eval_gateway(environ: dict[str, str]) -> tuple[str, str, list[str]]:
    """Read the gateway URL, optional bearer key and ordered model allowlist."""
    upstream_url = environ.get("GARDEN_EVAL_UPSTREAM_URL", "").strip().rstrip("/")
    if not upstream_url:
        raise StepFailed("GARDEN_EVAL_UPSTREAM_URL is not set.")
    try:
        parsed = urlsplit(upstream_url)
    except ValueError as error:
        raise StepFailed(
            "GARDEN_EVAL_UPSTREAM_URL must be a valid HTTP or HTTPS base URL."
        ) from error
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise StepFailed("GARDEN_EVAL_UPSTREAM_URL must be an HTTP or HTTPS base URL.")
    models: list[str] = []
    for part in environ.get("GARDEN_EVAL_MODELS", "").split(","):
        model = part.strip()
        if model and model not in models:
            if not MODEL_ID.fullmatch(model):
                raise StepFailed("GARDEN_EVAL_MODELS contains a model id dsh cannot use.")
            models.append(model)
    if not models:
        raise StepFailed("GARDEN_EVAL_MODELS must name at least one model.")
    return upstream_url, environ.get("GARDEN_EVAL_UPSTREAM_KEY", ""), models


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return None
    return float(value)


def _budget_reason(record: dict[str, object]) -> str | None:
    reason = record.get("budget_exceeded")
    if isinstance(reason, str):
        return reason
    failure_reason = record.get("failure_reason")
    if isinstance(failure_reason, str) and failure_reason.startswith("budget_exceeded:"):
        return failure_reason.split(":", 1)[1]
    return None


def _aborted_trial(
    *, run_id: str, task: EvalTask, model: str, repeat_number: int, requested_repeats: int
) -> dict[str, object]:
    return {
        "run_id": run_id,
        "trial_id": f"{task.id}/{model}/{repeat_number}",
        "task_id": task.id,
        "suite": task.suite,
        "area": task.area,
        "model": model,
        "repeat": repeat_number,
        "requested_repeats": requested_repeats,
        "passed": False,
        "failure_reason": "aborted_after_three_budget_failures",
        "error": None,
        "graders": [],
        "findings": [],
        "tool_calls": 0,
        "tokens": 0,
        "gateway_tokens": None,
        "duration_seconds": 0,
        "dsh_exit_code": None,
        "final_answer": "",
        "trace_id": None,
        "embedder": "none",
        "database": None,
        "database_kept": False,
        "budget_exceeded": None,
        "aborted": True,
        "confirmation": task.confirmation,
    }


def _cell_summary(
    task_id: str, model: str, records: list[dict[str, object]], repeat: int
) -> dict[str, object]:
    completed = [record for record in records if record.get("aborted") is not True]
    passed = sum(record.get("passed") is True for record in completed)
    metrics = {
        "tool_calls": [
            number
            for record in completed
            if (number := _number(record.get("tool_calls"))) is not None
        ],
        "tokens": [
            number for record in completed if (number := _number(record.get("tokens"))) is not None
        ],
        "duration_seconds": [
            number
            for record in completed
            if (number := _number(record.get("duration_seconds"))) is not None
        ],
    }
    medians = {
        name: round(statistics.median(values), 3) if values else None
        for name, values in metrics.items()
    }
    return {
        "task_id": task_id,
        "model": model,
        "repeat": repeat,
        "completed_trials": len(completed),
        "aborted_repeats": len(records) - len(completed),
        "budget_failures": sum(_budget_reason(record) is not None for record in completed),
        "pass_count": passed,
        "pass_rate": passed / len(completed) if completed else 0.0,
        "pass_k": len(completed) == repeat and passed == repeat,
        "median_tool_calls": medians["tool_calls"],
        "median_tokens": medians["tokens"],
        "median_duration_seconds": medians["duration_seconds"],
    }


def run_model_trials(
    tasks: list[EvalTask],
    models: list[str],
    *,
    repeat: int = 3,
    jobs: int = 2,
    run_id: str | None = None,
    output: Path,
    logins: dict[str, str] | None = None,
    environ: dict[str, str] | None = None,
    gateway_url: str = "",
    upstream_key: str = "",
    embedder: str = "none",
    keep_failed: bool = False,
    trial_runner: TrialRunner | None = None,
) -> tuple[dict[str, object], Path]:
    """Run the selected task/model cells and write a key-redacted JSON summary."""
    if repeat < 1 or jobs < 1:
        raise StepFailed("Repeat and jobs must both be positive integers.")
    if not tasks or not models:
        raise StepFailed("Select at least one eval task and one model.")
    if any(
        budget is not None and budget < 1
        for task in tasks
        for budget in (task.budget_tool_calls, task.budget_seconds, task.budget_tokens)
    ):
        raise StepFailed("Task budgets must be positive integers.")
    active_run_id = run_id or str(uuid.uuid4())
    started_at = datetime.now(UTC)
    active_runner = trial_runner or run_model_trial
    active_logins = {} if logins is None else logins
    active_env = {} if environ is None else environ
    telemetry = EvalTelemetry(active_env)
    cells = [(task, model) for task in tasks for model in models]

    def run_one_trial(task: EvalTask, model: str, repeat_number: int) -> dict[str, object]:
        trial_id = f"{task.id}/{model}/{repeat_number}"
        with telemetry.trial_span(
            run_id=active_run_id,
            trial_id=trial_id,
            task_id=task.id,
            suite=task.suite,
            area=task.area,
            model=model,
            repeat=repeat_number,
        ) as span:
            raw: dict[str, object]
            try:
                raw = active_runner(
                    run_id=active_run_id,
                    trial_id=trial_id,
                    task=task,
                    model=model,
                    repeat=repeat_number,
                    logins=active_logins,
                    environ=active_env,
                    gateway_url=gateway_url,
                    upstream_key=upstream_key,
                    embedder=embedder,
                    output=output,
                    keep_failed=keep_failed,
                )
            except Exception as error:
                raw = {
                    "passed": False,
                    "error": f"Trial runner failed: {error}",
                    "failure_reason": f"Trial runner failed: {error}",
                    "budget_exceeded": None,
                    "tool_calls": 0,
                    "tokens": 0,
                    "duration_seconds": 0,
                    "findings": [],
                    "graders": [],
                    "final_answer": "",
                }
            record: dict[str, object] = {
                "run_id": active_run_id,
                "trial_id": trial_id,
                "task_id": task.id,
                "suite": task.suite,
                "area": task.area,
                "model": model,
                "repeat": repeat_number,
                "requested_repeats": repeat,
                "passed": False,
                "error": None,
                "failure_reason": None,
                "graders": [],
                "findings": [],
                "tool_calls": 0,
                "tokens": 0,
                "gateway_tokens": None,
                "duration_seconds": 0,
                "dsh_exit_code": None,
                "final_answer": "",
                "trace_id": telemetry.trace_id(span),
                "budget_exceeded": None,
                "aborted": False,
                **raw,
            }
            record["trace_id"] = telemetry.trace_id(span)
            safe_record = cast(dict[str, object], _redact(record, upstream_key))
            passed = safe_record.get("passed") is True
            span.set_attribute("garden.eval.passed", passed)
            failure_reason = safe_record.get("failure_reason")
            if isinstance(failure_reason, str):
                span.set_attribute("garden.eval.failure_reason", failure_reason)
            telemetry.record_trial(safe_record)
            return safe_record

    next_repeat = [1] * len(cells)
    budget_failures = [0] * len(cells)
    aborted = [False] * len(cells)
    records_by_cell: list[dict[int, dict[str, object]]] = [{} for _ in cells]
    future_cells: dict[Future[dict[str, object]], tuple[int, int]] = {}
    next_cell = 0

    def fill_jobs(pool: ThreadPoolExecutor) -> None:
        nonlocal next_cell
        while len(future_cells) < jobs:
            cell_index = next(
                (
                    (next_cell + offset) % len(cells)
                    for offset in range(len(cells))
                    if not aborted[(next_cell + offset) % len(cells)]
                    and next_repeat[(next_cell + offset) % len(cells)] <= repeat
                ),
                None,
            )
            if cell_index is None:
                return
            task, model = cells[cell_index]
            repeat_number = next_repeat[cell_index]
            next_repeat[cell_index] += 1
            future = pool.submit(run_one_trial, task, model, repeat_number)
            future_cells[future] = (cell_index, repeat_number)
            next_cell = (cell_index + 1) % len(cells)

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        fill_jobs(pool)
        while future_cells:
            completed, _ = wait(future_cells, return_when=FIRST_COMPLETED)
            for future in completed:
                cell_index, repeat_number = future_cells.pop(future)
                record = future.result()
                records_by_cell[cell_index][repeat_number] = record
                if _budget_reason(record) is not None:
                    budget_failures[cell_index] += 1
                    if budget_failures[cell_index] >= 3:
                        aborted[cell_index] = True
            fill_jobs(pool)

    cell_records: list[list[dict[str, object]]] = []
    for index, (task, model) in enumerate(cells):
        records = records_by_cell[index]
        if aborted[index]:
            for repeat_number in range(1, repeat + 1):
                records.setdefault(
                    repeat_number,
                    _aborted_trial(
                        run_id=active_run_id,
                        task=task,
                        model=model,
                        repeat_number=repeat_number,
                        requested_repeats=repeat,
                    ),
                )
                if records[repeat_number].get("aborted") is True:
                    record = records[repeat_number]
                    trial_id = cast(str, record["trial_id"])
                    with telemetry.trial_span(
                        run_id=active_run_id,
                        trial_id=trial_id,
                        task_id=task.id,
                        suite=task.suite,
                        area=task.area,
                        model=model,
                        repeat=repeat_number,
                    ) as span:
                        record["trace_id"] = telemetry.trace_id(span)
                        span.set_attribute("garden.eval.passed", False)
                        span.set_attribute(
                            "garden.eval.failure_reason",
                            "aborted_after_three_budget_failures",
                        )
                        telemetry.record_trial(record)
        cell_records.append([records[repeat_number] for repeat_number in range(1, repeat + 1)])

    records = [record for group in cell_records for record in group]
    summaries = [
        _cell_summary(task.id, model, cell_records[index], repeat)
        for index, (task, model) in enumerate(cells)
    ]
    task_status: list[dict[str, object]] = []
    for task in tasks:
        task_cells = [cell for cell in summaries if cell["task_id"] == task.id]
        task_status.append(
            {
                "task_id": task.id,
                "check_task": bool(task_cells)
                and all(
                    cell["completed_trials"] == repeat and cell["pass_rate"] == 0.0
                    for cell in task_cells
                ),
            }
        )

    summary: dict[str, object] = {
        "run_id": active_run_id,
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "mode": "model",
        "repeat": repeat,
        "jobs": jobs,
        "models": models,
        "tasks": [task.id for task in tasks],
        "embedder": embedder,
        "passed": all(cell["pass_k"] is True for cell in summaries),
        "task_status": task_status,
        "cells": summaries,
        "trials": records,
    }
    safe_summary = cast(dict[str, object], _redact(summary, upstream_key))
    runs = output / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    path = runs / f"{active_run_id}.json"
    path.write_text(json.dumps(safe_summary, indent=2) + "\n", encoding="utf-8")
    if not telemetry.flush():
        print(
            f"OpenObserve export did not complete; the JSON summary remains at {path}.",
            file=sys.stderr,
        )
    return safe_summary, path


def print_model_summary(summary: dict[str, object], path: Path) -> None:
    print(f"Agent eval model run {summary['run_id']}")
    print(
        "Task                 Model                 Pass rate       Pass^k  Calls  Tokens  Seconds"
    )
    cells = summary.get("cells")
    if isinstance(cells, list):
        for cell_value in cast(list[object], cells):
            cell = _object(cell_value)
            if cell is None:
                continue
            task_id = str(cell.get("task_id", ""))
            model = str(cell.get("model", ""))
            completed_value = cell.get("completed_trials")
            completed = completed_value if isinstance(completed_value, int) else 0
            passed_value = cell.get("pass_count")
            passed = passed_value if isinstance(passed_value, int) else 0
            rate_value = _number(cell.get("pass_rate"))
            rate = round(rate_value * 100) if rate_value is not None else 0
            pass_rate = f"{passed}/{completed} ({rate}%)"
            pass_k = "yes" if cell.get("pass_k") is True else "no"
            calls = cell.get("median_tool_calls")
            tokens = cell.get("median_tokens")
            seconds = cell.get("median_duration_seconds")
            print(
                f"{task_id:<20} {model:<21} {pass_rate:<15} {pass_k:<7} "
                f"{calls if calls is not None else '-'!s:>5} "
                f"{tokens if tokens is not None else '-'!s:>7} "
                f"{seconds if seconds is not None else '-'!s:>8}"
            )
    task_status = summary.get("task_status")
    if isinstance(task_status, list):
        for status_value in cast(list[object], task_status):
            status = _object(status_value)
            if status is not None and status.get("check_task") is True:
                print(f"  {status.get('task_id')}: check the task")
    print(f"JSON summary: {path}")


def _model_env(
    *, gateway_url: str, upstream_key: str, model: str, token_budget: int
) -> dict[str, str]:
    return {
        "GARDEN_AGENT_MODEL_UPSTREAM_URL": gateway_url,
        "GARDEN_AGENT_MODEL_API_KEY": upstream_key,
        "GARDEN_AGENT_MODEL_MODELS": model,
        "GARDEN_AGENT_MODEL_DEFAULT": model,
        "GARDEN_AGENT_MODEL_TOKENS_PER_DAY": str(token_budget + 1),
    }


def _redact_log(path: Path, upstream_key: str) -> None:
    if not upstream_key or not path.exists():
        return
    content = path.read_text(encoding="utf-8", errors="replace")
    path.write_text(content.replace(upstream_key, "[REDACTED]"), encoding="utf-8")


def run_model_trial(
    *,
    run_id: str,
    trial_id: str,
    task: EvalTask,
    model: str,
    repeat: int,
    logins: dict[str, str],
    environ: dict[str, str],
    gateway_url: str,
    upstream_key: str,
    embedder: str = "none",
    output: Path,
    keep_failed: bool,
) -> dict[str, object]:
    """Run one real dsh trial in its own cloned database and sealed home."""
    started = time.monotonic()
    db_suffix = hashlib.sha256(trial_id.encode()).hexdigest()[:12]
    database = f"garden_agent_eval_{run_id[:8]}-{db_suffix}-{repeat}"
    log_path = output / "logs" / f"{run_id[:8]}-{db_suffix}-{repeat}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    services = Services()
    db_created = False
    db_kept = False
    answer = ""
    exit_code: int | None = None
    tool_calls = 0
    tokens = 0
    gateway_tokens: int | None = None
    gateway_usage_error: str | None = None
    findings: list[dict[str, object]] = []
    budget_exceeded: str | None = None
    error: str | None = None
    graders: list[GraderResult] = []
    try:
        runner.new_database(database, template=runner.TEMPLATE_DB)
        db_created = True
        model_env = _model_env(
            gateway_url=gateway_url,
            upstream_key=upstream_key,
            model=model,
            token_budget=(
                task.budget_tokens if task.budget_tokens is not None else DEFAULT_BUDGET_TOKENS
            ),
        )
        model_env[GEN_AI_CAPTURE_CONTENT_ENV] = "SPAN_ONLY"
        with tempfile.TemporaryDirectory(prefix=f"garden-eval-{db_suffix}-") as temp_name:
            temp = Path(temp_name)
            api_url = runner.start_api(
                database,
                temp / "files",
                log_path,
                environ,
                services,
                model_env=model_env,
                run_id=run_id,
                trial_id=trial_id,
            )
            command, dsh_env = prepare_dsh(
                task,
                portal_url=api_url,
                model=model,
                acting_token=logins["acting"],
                home=temp / "home",
                workspace=temp / "workspace",
                environ=environ,
                traceparent=EvalTelemetry.traceparent(),
            )
            result = run_dsh(
                command,
                cwd=temp / "workspace",
                env=dsh_env,
                budget_tool_calls=(
                    task.budget_tool_calls
                    if task.budget_tool_calls is not None
                    else DEFAULT_BUDGET_TOOL_CALLS
                ),
                budget_seconds=(
                    task.budget_seconds
                    if task.budget_seconds is not None
                    else DEFAULT_BUDGET_SECONDS
                ),
                budget_tokens=(
                    task.budget_tokens if task.budget_tokens is not None else DEFAULT_BUDGET_TOKENS
                ),
                upstream_key=upstream_key,
            )
            answer = result.final_answer
            exit_code = result.exit_code
            tool_calls = result.tool_calls
            tokens = result.tokens
            findings = result.findings
            budget_exceeded = result.budget_exceeded
            if result.stderr:
                with log_path.open("a", encoding="utf-8") as log:
                    log.write("dsh stderr:\n")
                    log.write(result.stderr)
            try:
                gateway_tokens = runner.gateway_usage_total(api_url, logins["acting"])
            except Exception as failure:
                gateway_usage_error = str(failure)
            graders = runner.grade_task(
                task,
                api_url,
                logins["grader"],
                answer=answer,
                reference=False,
            )
    except Exception as failure:
        error = f"Trial setup or dsh execution failed: {failure}"
        graders = [GraderResult(grader.id, "fail", error) for grader in task.graders]
    finally:
        try:
            services.stop_all()
        except Exception as failure:
            if error is None:
                error = f"Trial API cleanup failed: {failure}"

        passed = (
            error is None
            and budget_exceeded is None
            and exit_code == 0
            and runner.trial_passed(graders)
        )
        if db_created and keep_failed and not passed:
            db_kept = True
        elif db_created:
            try:
                runner.drop_owned_database(database)
            except Exception as failure:
                if error is None:
                    error = f"Trial database cleanup failed: {failure}"
                passed = False
        _redact_log(log_path, upstream_key)

    grader_failures = [result.reason for result in graders if result.verdict != "pass"]
    if budget_exceeded is not None:
        failure_reason = f"budget_exceeded:{budget_exceeded}"
    elif error is not None:
        failure_reason = error
    elif exit_code != 0:
        failure_reason = f"dsh exited with code {exit_code}."
    elif grader_failures:
        failure_reason = " ".join(grader_failures)
    else:
        failure_reason = None
    passed = (
        error is None
        and budget_exceeded is None
        and exit_code == 0
        and runner.trial_passed(graders)
    )
    return {
        "run_id": run_id,
        "trial_id": trial_id,
        "task_id": task.id,
        "suite": task.suite,
        "area": task.area,
        "model": model,
        "repeat": repeat,
        "passed": passed,
        "failure_reason": failure_reason,
        "error": error,
        "graders": [
            {"grader": result.grader, "verdict": result.verdict, "reason": result.reason}
            for result in graders
        ],
        "findings": findings,
        "tool_calls": tool_calls,
        "tokens": tokens,
        "gateway_tokens": gateway_tokens,
        "gateway_usage_error": gateway_usage_error,
        "duration_seconds": round(time.monotonic() - started, 3),
        "dsh_exit_code": exit_code,
        "final_answer": answer,
        "budget_exceeded": budget_exceeded,
        "aborted": False,
        "trace_id": None,
        "embedder": embedder,
        "database": database,
        "database_kept": db_kept,
        "confirmation": task.confirmation,
    }


def run_real_model_eval(
    tasks: list[EvalTask],
    *,
    selected_model: str | None = None,
    repeat: int = 3,
    jobs: int = 2,
    keep_failed: bool = False,
    environ: dict[str, str] | None = None,
) -> tuple[dict[str, object], Path]:
    """Validate local prerequisites, prepare the seeded template and run model trials."""
    env = dict(os.environ if environ is None else environ)
    upstream_url, upstream_key, configured_models = eval_gateway(env)
    safe_env = {name: value for name, value in env.items() if name != "GARDEN_EVAL_UPSTREAM_KEY"}
    if repeat < 1 or jobs < 1:
        raise StepFailed("Repeat and jobs must both be positive integers.")
    if not tasks:
        raise StepFailed("Select at least one eval task.")
    if any(
        budget is not None and budget < 1
        for task in tasks
        for budget in (task.budget_tool_calls, task.budget_seconds, task.budget_tokens)
    ):
        raise StepFailed("Task budgets must be positive integers.")
    if selected_model is not None:
        if selected_model not in configured_models:
            raise StepFailed("The selected model is not listed in GARDEN_EVAL_MODELS.")
        models = [selected_model]
    else:
        models = configured_models
    missing = runner.prereqs_missing(safe_env)
    if shutil.which("node", path=safe_env.get("PATH")) is None:
        missing.append("Node.js is not installed; dsh requires Node.js on PATH")
    if installed_dsh_entry(safe_env) is None:
        missing.append(
            "dsh is not installed; install Garden's dsh bundle before running model evals"
        )
    if missing:
        raise StepFailed(
            "Missing eval prerequisites:\n" + "\n".join(f"- {item}" for item in missing)
        )
    runner.start_stack(safe_env)
    output = runner.OUTPUT
    output.mkdir(parents=True, exist_ok=True)
    (output / "logs").mkdir(parents=True, exist_ok=True)
    template_task = next((task for task in tasks if task.requires_embedding), tasks[0])
    embedder_services = Services()
    try:
        embedder = "none"
        if any(task.requires_embedding for task in tasks):
            selected_embedder = ensure_embedding_server(
                safe_env,
                output / "logs",
                embedder_services,
                fake=False,
            )
            embedder = selected_embedder.embedder
            safe_env["GARDEN_EMBEDDING_URL"] = selected_embedder.url
        logins = runner.ensure_template(safe_env, output, template_task, embedder)
        return run_model_trials(
            tasks,
            models,
            repeat=repeat,
            jobs=jobs,
            output=output,
            logins=logins,
            environ=safe_env,
            gateway_url=upstream_url,
            upstream_key=upstream_key,
            embedder=embedder,
            keep_failed=keep_failed,
        )
    finally:
        embedder_services.stop_all()

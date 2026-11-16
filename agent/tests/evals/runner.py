"""Build isolated reference trials and record their complete results."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from garden.trace_contract import GARDEN_EVAL_RUN_ID_ENV, GARDEN_EVAL_TRIAL_ID_ENV
from tests.evals.grading import GraderResult, trial_passed
from tests.evals.harness import DshHarnessAdapter
from tests.evals.tasks import ClaimDraftTask, ReferenceSolution
from tests.shared.paths import AGENT_ROOT, REPO
from tests.shared.stack import Services, free_port, psql, wait_for
from tests.shared.transcript import StepFailed

TEMPLATE_DB = "garden_agent_eval_template"
OWNED_DB = re.compile(r"^garden_agent_eval_(?:template|[a-z0-9-]+)$")
OUTPUT = AGENT_ROOT / "dist" / "evals"
RUNS = OUTPUT / "runs"


def _as_object(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    return cast(dict[str, object], value)


def prereqs_missing(environ: dict[str, str]) -> list[str]:
    missing = [name for name in ("docker", "just", "uv") if shutil.which(name) is None]
    if not environ.get("GARDEN_DATABASE_URL"):
        missing.append("GARDEN_DATABASE_URL is not set; run 'just setup' or set it in .env")
    try:
        database = urlsplit(environ.get("GARDEN_DATABASE_URL", ""))
        if database.scheme not in {"postgresql", "postgresql+psycopg"}:
            missing.append("GARDEN_DATABASE_URL must use PostgreSQL")
        elif database.hostname not in {"localhost", "127.0.0.1"}:
            missing.append("GARDEN_DATABASE_URL must point to the local Compose database")
        else:
            expected_port = int(environ.get("GARDEN_DEV_POSTGRES_PORT", "55432"))
            if database.port != expected_port:
                missing.append(
                    "GARDEN_DATABASE_URL must use the Compose port in GARDEN_DEV_POSTGRES_PORT"
                )
    except ValueError:
        missing.append("GARDEN_DATABASE_URL is invalid")
    if environ.get("GARDEN_ENVIRONMENT", "dev") != "dev":
        missing.append("GARDEN_ENVIRONMENT must be dev for local evals")
    issuer = urlsplit(environ.get("GARDEN_OIDC_ISSUER", "http://127.0.0.1:55081/default"))
    try:
        expected_oidc_port = int(environ.get("GARDEN_DEV_OIDC_PORT", "55081"))
        if (
            issuer.scheme != "http"
            or issuer.hostname not in {"localhost", "127.0.0.1"}
            or issuer.port != expected_oidc_port
        ):
            missing.append("GARDEN_OIDC_ISSUER must point to the local Compose identity provider")
    except ValueError:
        missing.append("GARDEN_OIDC_ISSUER is invalid")
    if shutil.which("docker") is not None:
        try:
            result = subprocess.run(
                ["docker", "info", "--format", "{{.ServerVersion}}"],
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
            )
        except (OSError, subprocess.TimeoutExpired):
            result = None
        if result is None or result.returncode != 0:
            missing.append("Docker is installed, but its daemon is not reachable")
    return missing


def _run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: float = 600,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def start_stack(env: dict[str, str]) -> None:
    try:
        result = _run(["just", "up"], cwd=REPO, env=env, timeout=1200)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise StepFailed(
            f"Could not start the Garden dev stack with 'just up': {error}."
        ) from error
    if result.returncode != 0:
        output = (result.stdout + result.stderr).strip()
        raise StepFailed(f"Could not start the Garden dev stack with 'just up':\n{output[-4000:]}")
    issuer = env.get("GARDEN_OIDC_ISSUER", "http://127.0.0.1:55081/default").rstrip("/")
    wait_for(f"{issuer}/.well-known/openid-configuration", timeout=120)


def _database_url(database: str, env: dict[str, str]) -> str:
    parsed = urlsplit(env["GARDEN_DATABASE_URL"])
    return urlunsplit((parsed.scheme, parsed.netloc, f"/{database}", parsed.query, parsed.fragment))


def _template_fingerprint(task: ClaimDraftTask) -> str:
    backend = REPO / "backend"
    paths = [backend / "app" / "seed.py", backend / "app" / "composition.py"]
    paths.extend(sorted((backend / "migrations").rglob("*.py")))
    paths.extend(sorted((backend / "app").rglob("permissions.py")))
    digest = hashlib.sha256(b"garden-agent-eval-template-v1\n")
    digest.update(task.asking_person.encode())
    for path in sorted(set(paths)):
        digest.update(path.relative_to(backend).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _database_exists(name: str) -> bool:
    return psql("postgres", f"select count(*) from pg_database where datname = '{name}'") == "1"


def _drop_owned_database(name: str) -> None:
    if not OWNED_DB.fullmatch(name):
        raise StepFailed(f"Refusing to drop a database not owned by this eval runner: {name}.")
    psql("postgres", f'drop database if exists "{name}" with (force)')


def _new_database(name: str, *, template: str | None = None) -> None:
    if not OWNED_DB.fullmatch(name):
        raise StepFailed(f"Refusing to create a database outside the eval namespace: {name}.")
    clause = f' with template "{template}"' if template else ""
    psql("postgres", f'create database "{name}"{clause}')


def _template_marker() -> str:
    return psql(
        "postgres",
        "select coalesce(shobj_description(oid, 'pg_database'), '') "
        f"from pg_database where datname = '{TEMPLATE_DB}'",
    )


def _backend_env(
    database: str,
    files_root: Path,
    base_env: dict[str, str],
    *,
    run_id: str | None = None,
    trial_id: str | None = None,
) -> dict[str, str]:
    env = {key: value for key, value in base_env.items() if not key.startswith("GARDEN_")}
    env.update(
        {
            "GARDEN_ENVIRONMENT": "dev",
            "GARDEN_DATABASE_URL": _database_url(database, base_env),
            "GARDEN_FAKE_OIDC": "true",
            "GARDEN_OIDC_ISSUER": base_env.get(
                "GARDEN_OIDC_ISSUER", "http://127.0.0.1:55081/default"
            ),
            "GARDEN_OIDC_CLIENT_ID": base_env.get("GARDEN_OIDC_CLIENT_ID", "garden-dev"),
            "GARDEN_OIDC_CLIENT_SECRET": base_env.get(
                "GARDEN_OIDC_CLIENT_SECRET", "garden-dev-secret"
            ),
            "GARDEN_PUBLIC_URL": base_env.get("GARDEN_PUBLIC_URL", "http://localhost:55173"),
            "GARDEN_FILES_ROOT": str(files_root),
        }
    )
    if run_id is not None:
        env[GARDEN_EVAL_RUN_ID_ENV] = run_id
    if trial_id is not None:
        env[GARDEN_EVAL_TRIAL_ID_ENV] = trial_id
    return env


def _start_api(
    database: str,
    files_root: Path,
    log: Path,
    base_env: dict[str, str],
    services: Services,
    *,
    run_id: str | None = None,
    trial_id: str | None = None,
) -> str:
    port = free_port()
    files_root.mkdir(parents=True, exist_ok=True)
    process = services.start(
        ["uv", "run", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=REPO / "backend",
        env=_backend_env(database, files_root, base_env, run_id=run_id, trial_id=trial_id),
        log=log,
    )
    url = f"http://127.0.0.1:{port}"
    wait_for(f"{url}/api/health", timeout=120)
    if process.poll() is not None:
        raise StepFailed(f"The trial API exited during startup. See {log}.")
    return url


def _request_json(
    url: str,
    *,
    method: str,
    body: dict[str, object] | None = None,
    cookie: str | None = None,
) -> tuple[int, object]:
    data = None if body is None else json.dumps(body).encode()
    request = Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    if cookie is not None:
        request.add_header("Cookie", cookie)
    try:
        with urlopen(request, timeout=30) as response:
            return response.status, cast(object, json.loads(response.read()))
    except HTTPError as error:
        content = error.read()
        try:
            response = cast(object, json.loads(content or b"null"))
        except json.JSONDecodeError:
            response = {"detail": content.decode("utf-8", "replace")}
        return error.code, response
    except (URLError, TimeoutError, json.JSONDecodeError) as error:
        raise StepFailed(
            f"Could not create a trial Agent login through the API: {error}."
        ) from error


def _create_agent_login(api_url: str, database: str, subject: str, device: str) -> str:
    browser_token = secrets.token_urlsafe(32)
    hashed = hashlib.sha256(browser_token.encode()).hexdigest()
    escaped_subject = subject.replace("'", "''")
    psql(
        database,
        "insert into users.web_session (token_hash, person_id, created_at, last_seen_at) "
        f"select '{hashed}', id, now(), now() from users.person "
        f"where subject = '{escaped_subject}'",
    )
    if (
        psql(
            database,
            f"select count(*) from users.web_session where token_hash = '{hashed}'",
        )
        != "1"
    ):
        raise StepFailed(f"The seeded person {subject!r} was not found for the trial Agent login.")
    status, response = _request_json(
        f"{api_url}/api/agent/my-logins",
        method="POST",
        cookie=f"garden_session={browser_token}",
        body={"device": device, "expires_in_days": 30},
    )
    response_object = _as_object(response)
    token = response_object.get("token") if response_object is not None else None
    if status != 201 or not isinstance(token, str):
        raise StepFailed(
            f"The API could not create the trial Agent login for {subject!r} (HTTP {status})."
        )
    return token


def ensure_template(env: dict[str, str], output: Path, task: ClaimDraftTask) -> dict[str, str]:
    fingerprint = _template_fingerprint(task)
    login_file = output / "template-logins.json"
    if _template_marker() == fingerprint and login_file.is_file():
        try:
            logins = _as_object(cast(object, json.loads(login_file.read_text(encoding="utf-8"))))
            if (
                logins is not None
                and logins.get("acting_subject") == task.asking_person
                and isinstance(logins.get("acting"), str)
                and isinstance(logins.get("grader"), str)
            ):
                acting = logins["acting"]
                grader = logins["grader"]
                assert isinstance(acting, str) and isinstance(grader, str)
                return {"acting": acting, "grader": grader}
        except (OSError, json.JSONDecodeError):
            pass

    if _database_exists(TEMPLATE_DB):
        _drop_owned_database(TEMPLATE_DB)
    _new_database(TEMPLATE_DB)
    try:
        template_env = _backend_env(TEMPLATE_DB, output / "template-files", env)
        for module in ("app.migrate", "app.seed"):
            result = _run(
                ["uv", "run", "python", "-m", module],
                cwd=REPO / "backend",
                env=template_env,
                timeout=900,
            )
            if result.returncode != 0:
                raise StepFailed(
                    f"Template setup failed in {module}:\n{(result.stdout + result.stderr)[-4000:]}"
                )

        with tempfile.TemporaryDirectory(prefix="garden-eval-template-") as temp_name:
            temp = Path(temp_name)
            services = Services()
            try:
                api_url = _start_api(
                    TEMPLATE_DB,
                    temp / "files",
                    temp / "api.log",
                    env,
                    services,
                )
                logins = {
                    "acting_subject": task.asking_person,
                    "acting": _create_agent_login(
                        api_url,
                        TEMPLATE_DB,
                        task.asking_person,
                        "garden-eval-task",
                    ),
                    "grader": _create_agent_login(
                        api_url,
                        TEMPLATE_DB,
                        "dev.admin@fernhill.example",
                        "garden-eval-grader",
                    ),
                }
            finally:
                services.stop_all()

        psql(
            "postgres",
            f"comment on database \"{TEMPLATE_DB}\" is 'garden-agent-eval-template:{fingerprint}'",
        )
        login_file.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(login_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(logins, handle)
            handle.write("\n")
        login_file.chmod(0o600)
        return logins
    except Exception:
        _drop_owned_database(TEMPLATE_DB)
        raise


def _grade(task: ClaimDraftTask, api_url: str, grader_token: str) -> list[GraderResult]:
    results: list[GraderResult] = []
    for grader in task.graders:
        results.append(grader.grade(api_url, grader_token, task))
    return results


def _trial_record(
    *,
    run_id: str,
    task: ClaimDraftTask,
    variant: str,
    expected_pass: bool,
    started: float,
    database: str,
    db_kept: bool,
    reference_solution: str,
    answer: str,
    exit_code: int,
    tool_calls: int,
    graders: list[GraderResult],
    error: str | None,
) -> dict[str, object]:
    passed = error is None and trial_passed(graders)
    failures = [result.reason for result in graders if result.verdict != "pass"]
    failure_reason = error or (" ".join(failures) if failures else None)
    return {
        "run_id": run_id,
        "trial_id": f"{run_id[:8]}-{variant}",
        "task_id": task.id,
        "suite": task.suite,
        "area": task.area,
        "model": "reference",
        "repeat": 1,
        "reference_solution": reference_solution,
        "expected_pass": expected_pass,
        "passed": passed,
        "failure_reason": failure_reason,
        "error": error,
        "graders": [
            {"grader": result.grader, "verdict": result.verdict, "reason": result.reason}
            for result in graders
        ],
        "findings": [],
        "tool_calls": tool_calls,
        "tokens": 0,
        "duration_seconds": round(time.monotonic() - started, 3),
        "dsh_exit_code": exit_code,
        "final_answer": answer,
        "trace_id": None,
        "embedder": "none",
        "database": database,
        "database_kept": db_kept,
        "confirmation": task.confirmation,
    }


def run_trial(
    *,
    run_id: str,
    task: ClaimDraftTask,
    variant: str,
    expected_pass: bool,
    solution: ReferenceSolution,
    logins: dict[str, str],
    env: dict[str, str],
    output: Path,
    keep_failed: bool,
) -> dict[str, object]:
    started = time.monotonic()
    trial_id = f"{run_id[:8]}-{variant}"
    database = f"garden_agent_eval_{trial_id}"
    db_created = False
    db_kept = False
    answer = ""
    exit_code = 1
    tool_calls = 0
    error: str | None = None
    graders: list[GraderResult] = []
    services = Services()
    try:
        _new_database(database, template=TEMPLATE_DB)
        db_created = True
        with tempfile.TemporaryDirectory(prefix=f"garden-eval-{trial_id}-") as temp_name:
            temp = Path(temp_name)
            api_url = _start_api(
                database,
                temp / "files",
                output / "logs" / f"{trial_id}.log",
                env,
                services,
                run_id=run_id,
                trial_id=trial_id,
            )
            adapter: DshHarnessAdapter | None = None
            try:
                adapter = DshHarnessAdapter(
                    task,
                    portal_url=api_url,
                    home=temp / "home",
                    workspace=temp / "workspace",
                    acting_token=logins["acting"],
                )
                answer = adapter.run_reference(solution)
                exit_code = 0
            except Exception as failure:
                error = f"Reference solution failed: {failure}"
            finally:
                if adapter is not None:
                    tool_calls = adapter.tool_calls
                    try:
                        adapter.close()
                    except Exception as failure:
                        if error is None:
                            error = f"Harness cleanup failed: {failure}"
            graders = _grade(task, api_url, logins["grader"])
    except Exception as failure:
        error = f"Trial setup failed: {failure}"
        graders = [
            GraderResult(grader.id, "fail", f"The trial could not run: {failure}")
            for grader in task.graders
        ]
    finally:
        try:
            services.stop_all()
        except Exception as failure:
            if error is None:
                error = f"Trial API cleanup failed: {failure}"

    passed = error is None and trial_passed(graders)
    if db_created and keep_failed and not passed:
        db_kept = True
    elif db_created:
        try:
            _drop_owned_database(database)
        except Exception as failure:
            if error is None:
                error = f"Trial database cleanup failed: {failure}"
            passed = False

    return _trial_record(
        run_id=run_id,
        task=task,
        variant=variant,
        expected_pass=expected_pass,
        started=started,
        database=database,
        db_kept=db_kept,
        reference_solution=variant,
        answer=answer,
        exit_code=exit_code,
        tool_calls=tool_calls,
        graders=graders,
        error=error,
    )


def run_reference(
    task: ClaimDraftTask,
    *,
    keep_failed: bool = False,
    environ: dict[str, str] | None = None,
) -> tuple[dict[str, object], Path]:
    started_at = datetime.now(UTC)
    env = dict(os.environ if environ is None else environ)
    missing = prereqs_missing(env)
    if missing:
        raise StepFailed(
            "Missing eval prerequisites:\n" + "\n".join(f"- {item}" for item in missing)
        )
    start_stack(env)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    RUNS.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "logs").mkdir(parents=True, exist_ok=True)
    logins = ensure_template(env, OUTPUT, task)
    run_id = str(uuid.uuid4())
    records: list[dict[str, object]] = []
    for variant, expected_pass, solution in (
        ("perfect", True, task.perfect),
        ("wrong", False, task.wrong),
    ):
        if solution is None:
            raise StepFailed(f"Task {task.id!r} has no {variant} reference solution.")
        try:
            record = run_trial(
                run_id=run_id,
                task=task,
                variant=variant,
                expected_pass=expected_pass,
                solution=solution,
                logins=logins,
                env=env,
                output=OUTPUT,
                keep_failed=keep_failed,
            )
        except Exception as failure:
            record = _trial_record(
                run_id=run_id,
                task=task,
                variant=variant,
                expected_pass=expected_pass,
                started=time.monotonic(),
                database=f"garden_agent_eval_{run_id[:8]}-{variant}",
                db_kept=False,
                reference_solution=variant,
                answer="",
                exit_code=1,
                tool_calls=0,
                graders=[
                    GraderResult(grader.id, "fail", f"The trial runner failed: {failure}")
                    for grader in task.graders
                ],
                error=f"Trial runner failed: {failure}",
            )
        records.append(record)
    passed = all(
        record["passed"] is record["expected_pass"] and record["error"] is None
        for record in records
    )
    summary: dict[str, object] = {
        "run_id": run_id,
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "mode": "reference",
        "task": {
            "id": task.id,
            "prompt": task.prompt,
            "asking_person": task.asking_person,
            "confirmation": task.confirmation,
            "suite": task.suite,
            "area": task.area,
            "graders": [grader.id for grader in task.graders],
            "budget_tool_calls": task.budget_tool_calls,
            "budget_seconds": task.budget_seconds,
            "budget_tokens": task.budget_tokens,
            "web_tools_off": task.web_tools_off,
        },
        "passed": passed,
        "trials": records,
    }
    path = RUNS / f"{run_id}.json"
    path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary, path


def print_summary(summary: dict[str, object], path: Path) -> None:
    print(f"Agent eval reference run {summary['run_id']}")
    print("Task                 Case      Expected  Result")
    trials = summary.get("trials")
    if isinstance(trials, list):
        for record_value in cast(list[object], trials):
            record = _as_object(record_value)
            if record is None:
                continue
            expected = "pass" if record.get("expected_pass") is True else "fail"
            result = "pass" if record.get("passed") is True else "fail"
            task_id = str(record.get("task_id", ""))
            reference_solution = str(record.get("reference_solution", ""))
            print(f"{task_id:<20} {reference_solution:<9} {expected:<9} {result}")
            failure_reason = record.get("failure_reason")
            if failure_reason:
                print(f"  {failure_reason}")
    print(f"JSON summary: {path}")

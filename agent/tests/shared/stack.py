"""The stack a test run starts and stops: its ports, its processes and the portal calls."""

from __future__ import annotations

import json
import os
import re
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from garden.auth import load_session
from garden.shared import Settings
from tests.shared.paths import REPO
from tests.shared.transcript import Run, StepFailed

API_PORT = 56000


WEB_PORT = 56173


PORTAL = f"http://127.0.0.1:{API_PORT}"


WEB = f"http://localhost:{WEB_PORT}"


PERSON_NAME = "Dev Admin"


PERSON_SUBJECT = "dev.admin@fernhill.example"


DB_NAME = re.compile(r"^garden_e2e\d+_test$")


@dataclass
class Services:
    """Every process this run starts. They run in their own sessions, so each one can be stopped
    with its group."""

    procs: list[subprocess.Popen[bytes]] = field(default_factory=list[subprocess.Popen[bytes]])

    def start(
        self, args: list[str], *, cwd: Path, env: dict[str, str], log: Path
    ) -> subprocess.Popen[bytes]:
        handle = log.open("w", encoding="utf-8")
        proc = subprocess.Popen(
            args, cwd=cwd, env=env, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True
        )
        self.procs.append(proc)
        return proc

    def stop_all(self) -> None:
        for proc in self.procs:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
        for proc in self.procs:
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=20)


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def require_port_free(port: int, what: str) -> None:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            pass
    except OSError:
        return
    raise StepFailed(
        f"Port {port} ({what}) is already in use. Stop that process, or pick another port."
    )


def wait_for(url: str, timeout: float = 120.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5):
                return
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
    raise StepFailed(f"Nothing answered at {url} within {int(timeout)} seconds.")


def api(method: str, path: str, token: str | None = None, body: Any = None) -> tuple[int, str]:
    """One portal call. Returns the status and the body, and never raises on an HTTP error."""
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(f"{PORTAL}{path}", data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    if token is not None:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")


def psql(database: str, sql: str) -> str:
    """One query in the Compose Postgres container, for the database this run owns."""
    command = [
        "docker",
        "compose",
        "-f",
        str(REPO / "dev" / "compose.yaml"),
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        "garden",
        "-d",
        database,
        "-v",
        "ON_ERROR_STOP=1",
        "-tAc",
        sql,
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=120)
    if result.returncode != 0:
        raise StepFailed(f"psql failed: {result.stderr.strip()}")
    return result.stdout.strip()


def read_token(target: Path) -> str:
    """The CLI token from the test-only secret file. It is returned, never printed."""
    secrets_file = (
        target if target.is_file() or target.suffix == ".json" else target / "secrets.json"
    )
    home_dir = target if target.is_dir() else target.parent
    old_store = os.environ.get("GARDEN_SECRET_STORE")
    os.environ["GARDEN_SECRET_STORE"] = f"file:{secrets_file}"
    try:
        settings = Settings(portal_url=PORTAL, home=home_dir, dsh_home=home_dir / ".dsh")
        session = load_session(settings)
        if session is None:
            raise StepFailed(f"Could not load session from {target}")
        return session.token
    finally:
        if old_store is not None:
            os.environ["GARDEN_SECRET_STORE"] = old_store
        else:
            os.environ.pop("GARDEN_SECRET_STORE", None)


def drop_database(database: str, run: Run) -> None:
    """Drops the database this run created, and only that one. The dev database is never named
    here."""
    if not DB_NAME.match(database):
        return
    try:
        psql(
            "postgres",
            f'drop database if exists "{database}" with (force)',
        )
        run.say(f"Dropped {database}.")
    except StepFailed as error:
        run.say(f"Could not drop {database}: {error}")
